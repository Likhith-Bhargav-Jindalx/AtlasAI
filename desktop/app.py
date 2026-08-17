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


_load_production_env()

import uvicorn
import webview
from fastapi.staticfiles import StaticFiles

from atlas_engine.api import app as fastapi_app


def _run_server() -> None:
    uvicorn.run(fastapi_app, host="127.0.0.1", port=PORT, log_level="info")


def main() -> None:
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


if __name__ == "__main__":
    main()
