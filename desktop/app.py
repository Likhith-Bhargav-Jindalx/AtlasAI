"""Desktop shell entrypoint: runs a FastAPI app locally and shows the built
frontend in a native pywebview window instead of a browser tab.

Always runs the full atlas_engine FastAPI app in-process — this exe talks
directly to Postgres and Azure Blob Storage, with no deployed backend in
between. In dev (unfrozen), that means whatever engine/.env points at
(local Postgres/disk, same as `npm run dev` + `uvicorn` today). In a
packaged build, desktop/production.env is baked into the bundle at build
time (see desktop/build.sh) and loaded here before atlas_engine.config's
Settings singleton is constructed, so every double-clicked copy of the exe
connects straight to the real production database and blob storage —
no server to deploy, no setup step for whoever runs it.
"""

import os
import shutil
import sys
import threading
import time
from pathlib import Path

# Must be set before Playwright is imported anywhere in the process (the
# atlas_engine import below pulls it in transitively). "0" tells Playwright
# to look for its browsers inside its own package installation instead of
# the machine's global cache (~/.cache/ms-playwright or platform equivalent)
# — the browser was installed there for exactly this reason (see the
# desktop exe packaging design doc), so a packaged build is fully
# self-contained and doesn't depend on the target machine already having
# Chromium installed.
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

PORT = int(os.environ.get("ATLAS_DESKTOP_PORT", "8000"))


def _resource_path(relative: str) -> Path:
    """Resolves a bundled resource both when run unfrozen (dev testing,
    relative to this file) and when frozen by PyInstaller (relative to
    sys._MEIPASS, the bundle's extraction dir)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def _read_app_version() -> str:
    return _resource_path("VERSION").read_text().strip()


def _load_production_env() -> None:
    """Only runs in a frozen (packaged) build. Reads the production.env
    baked into the bundle by desktop/build.sh and sets each key via
    setdefault — so a developer's own real environment variables (there
    shouldn't be any in a frozen build, but just in case) always win."""
    if not getattr(sys, "frozen", False):
        return

    env_path = _resource_path("production.env")
    if not env_path.exists():
        raise RuntimeError(
            f"Packaged build is missing {env_path} — this exe was built without baked-in "
            "production credentials (desktop/production.env). Rebuild via desktop/build.sh "
            "after creating that file; see desktop/production.env.example."
        )

    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _configure_log_file() -> None:
    """A double-clicked .app has no terminal attached — stdout (where
    atlas_engine.observability.configure_logging writes by default) goes
    nowhere anyone can ever read. Every crash, including one inside the
    live-capture background thread, is otherwise unrecoverable for
    whoever's running the exe. Must run before atlas_engine.api is
    imported below, since that import triggers configure_logging() at
    module load time."""
    log_path = Path.home() / ".atlas" / "logs" / "atlas-desktop.log"
    os.environ.setdefault("ATLAS_LOG_FILE", str(log_path))


def _cleanup_stale_vendor_metadata() -> None:
    """The self-update swap's robocopy is deliberately non-mirroring (see
    write_swap_script) so an app-only update doesn't wipe the untouched
    playwright/ folder — but that means a vendor update can leave the
    *previous* version's playwright-X.Y.Z.dist-info directory sitting
    next to the new one it doesn't overwrite (dist-info dirs are named
    after their version, so they never collide). Two dist-infos for the
    same package makes importlib.metadata.version("playwright") resolve
    to whichever one it finds, not necessarily the real one — confirmed
    live via Sentry reporting a stale playwright version after an update
    that had actually landed. Only the one matching the code actually on
    disk (read from the package itself) is correct."""
    if not getattr(sys, "frozen", False):
        return
    try:
        from playwright._repo_version import version as real_version
    except Exception:
        return
    current_name = f"playwright-{real_version}.dist-info"
    for entry in _resource_path(".").glob("playwright-*.dist-info"):
        if entry.name != current_name:
            shutil.rmtree(entry, ignore_errors=True)


_load_production_env()
_configure_log_file()
_cleanup_stale_vendor_metadata()

from atlas_engine.observability import init_error_tracking

init_error_tracking(release=_read_app_version())

import dataclasses
import importlib.metadata
import tempfile

import uvicorn
import webview
from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles

from atlas_engine.api import app as fastapi_app
from atlas_engine.desktop_update import (
    UpdateApplyError,
    check_for_update,
    launch_swap_script,
    stage_update,
    write_swap_script,
)


def _run_server() -> None:
    uvicorn.run(fastapi_app, host="127.0.0.1", port=PORT, log_level="info")


def _read_vendor_version() -> str:
    """Reads playwright's version straight from the installed package
    rather than importlib.metadata, which can resolve to a stale
    playwright-X.Y.Z.dist-info left behind by a self-update (see
    _cleanup_stale_vendor_metadata) — this stays correct even before that
    cleanup has had a chance to run."""
    try:
        from playwright._repo_version import version

        return version
    except Exception:
        return importlib.metadata.version("playwright")


@fastapi_app.get("/update/check")
def update_check_endpoint() -> dict:
    app_version = _read_app_version()
    vendor_version = _read_vendor_version()
    result = check_for_update(app_version, vendor_version, connection_string=os.environ.get("ATLAS_AZURE_STORAGE_CONNECTION_STRING"))
    # Surfaces what this exact install is actually running — the only way to
    # confirm from outside the machine that a self-update really swapped in
    # the files it claimed to, instead of digging through _internal/ by hand.
    return {**dataclasses.asdict(result), "app_version": app_version, "vendor_version": vendor_version}


_update_state_lock = threading.Lock()
_update_progress: dict = {"phase": "idle", "bytes_downloaded": 0, "total_bytes": None, "error": None}


def _update_progress_snapshot() -> dict:
    with _update_state_lock:
        return dict(_update_progress)


def _set_update_progress(**fields) -> None:
    with _update_state_lock:
        _update_progress.update(fields)


def _on_download_progress(phase: str, bytes_downloaded: int, total_bytes: int | None) -> None:
    _set_update_progress(phase=f"downloading_{phase}", bytes_downloaded=bytes_downloaded, total_bytes=total_bytes)


def _run_update_in_background(result) -> None:
    staging_dir = Path(tempfile.mkdtemp(prefix="atlas-desktop-update-"))
    try:
        stage_update(
            result,
            staging_dir,
            connection_string=os.environ["ATLAS_AZURE_STORAGE_CONNECTION_STRING"],
            on_progress=_on_download_progress,
        )
    except UpdateApplyError as exc:
        _set_update_progress(phase="error", error=str(exc))
        return

    _set_update_progress(phase="relaunching", bytes_downloaded=0, total_bytes=None, error=None)

    install_dir = Path(sys.executable).resolve().parent
    script_path = staging_dir.parent / f"{staging_dir.name}-swap.ps1"
    write_swap_script(
        pid=os.getpid(),
        staging_dir=staging_dir,
        install_dir=install_dir,
        exe_name="atlas-desktop.exe",
        script_path=script_path,
    )
    launch_swap_script(script_path)

    # Exit after a short delay, not immediately — the frontend's last
    # /update/progress poll must land before this process disappears,
    # otherwise the swap looks like it silently failed.
    threading.Timer(1.0, lambda: os._exit(0)).start()


@fastapi_app.post("/update/apply")
def update_apply_endpoint():
    if not getattr(sys, "frozen", False):
        raise HTTPException(status_code=400, detail="updates only apply to a packaged build")

    if _update_progress_snapshot()["phase"] not in ("idle", "error"):
        raise HTTPException(status_code=409, detail="update already in progress")

    result = check_for_update(
        _read_app_version(), _read_vendor_version(), connection_string=os.environ.get("ATLAS_AZURE_STORAGE_CONNECTION_STRING")
    )
    if not result.update_available:
        raise HTTPException(status_code=409, detail="no update available")

    _set_update_progress(phase="downloading_app", bytes_downloaded=0, total_bytes=None, error=None)
    threading.Thread(target=_run_update_in_background, args=(result,), daemon=True).start()
    return {"ok": True}


@fastapi_app.get("/update/progress")
def update_progress_endpoint() -> dict:
    return _update_progress_snapshot()


def _unblock_windows_bundle() -> None:
    """A zip downloaded via a browser gets tagged by Windows as
    "Internet zone" (the Zone.Identifier NTFS alternate-data-stream, aka
    Mark-of-the-Web) — and extracting it propagates that tag onto every
    extracted file. .NET Framework's CLR loader refuses to resolve
    exports from an Internet-zone assembly, which is why pywebview's
    Windows backend (pythonnet -> clr_loader -> Python.Runtime.dll) fails
    with "Failed to resolve Python.Runtime.Loader.Initialize" the first
    time someone runs this exe straight out of their Downloads folder,
    unless they first right-click -> Properties -> Unblock the zip.
    Stripping the stream from every bundled file before webview.start()
    below loads pythonnet makes the exe self-healing instead of relying
    on every recipient knowing to unblock it manually."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    bundle_root = Path(sys.executable).resolve().parent
    for path in bundle_root.rglob("*"):
        if path.is_file():
            try:
                os.remove(f"{path}:Zone.Identifier")
            except OSError:
                pass


def main() -> None:
    try:
        _unblock_windows_bundle()
        frontend_dist = _resource_path("frontend_dist")

        # Mounted after the app's own routes are already registered at import
        # time, so /health, /auth/login, etc. still win — this / mount only
        # catches what no route matched (the SPA's static assets).
        fastapi_app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

        threading.Thread(target=_run_server, daemon=True).start()
        time.sleep(1.5)

        # "localhost", not "127.0.0.1" — the frontend's compiled JS calls
        # http://localhost:8000 by default (VITE_API_BASE_URL's fallback), and
        # browsers treat 127.0.0.1 and localhost as different origins even
        # though they resolve to the same machine. Loading the window from a
        # different origin than the frontend's own fetch calls turns every API
        # request into a cross-origin one the CORS allowlist doesn't cover.
        webview.create_window("Atlas AI", f"http://localhost:{PORT}/", width=1280, height=800)
        webview.start()
    except Exception as exc:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
        raise


if __name__ == "__main__":
    main()
