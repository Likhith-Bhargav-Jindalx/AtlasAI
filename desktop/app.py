"""Desktop shell entrypoint: runs a FastAPI app locally and shows the built
frontend in a native pywebview window instead of a browser tab.

Two modes, chosen by whether ATLAS_REMOTE_BACKEND_URL is set:
- Unset (default): runs the full atlas_engine FastAPI app locally, exactly
  like `npm run dev` + `uvicorn` do today, targeting local Postgres/storage.
- Set: runs remote_app's thin local server instead — it drives the browser
  capture on this machine (that part is inherently local) but submits
  finished recordings to the deployed backend over HTTPS and proxies
  everything else there too, so this machine never needs direct database
  or blob storage credentials. See docs/superpowers/specs/
  2026-08-17-desktop-exe-packaging-design.md for the full design.
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

import uvicorn
import webview
from fastapi.staticfiles import StaticFiles

PORT = int(os.environ.get("ATLAS_DESKTOP_PORT", "8000"))
REMOTE_BACKEND_URL = os.environ.get("ATLAS_REMOTE_BACKEND_URL")


def _resource_path(relative: str) -> Path:
    """Resolves a bundled resource both when run unfrozen (dev testing,
    relative to this file) and when frozen by PyInstaller (relative to
    sys._MEIPASS, the bundle's extraction dir)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def _build_fastapi_app():
    if REMOTE_BACKEND_URL:
        # A plain sibling-module import, not desktop.remote_app: app.py
        # runs as a script (unfrozen: python's own directory-of-the-script
        # rule adds desktop/ to sys.path; frozen: PyInstaller's Analysis
        # bundles it the same way), so remote_app is importable by its own
        # name, not as a package attribute.
        from remote_app import build_app

        return build_app(REMOTE_BACKEND_URL)

    from atlas_engine.api import app as local_app

    return local_app


def _run_server(fastapi_app) -> None:
    uvicorn.run(fastapi_app, host="127.0.0.1", port=PORT, log_level="info")


def main() -> None:
    fastapi_app = _build_fastapi_app()
    frontend_dist = _resource_path("frontend_dist")

    # Mounted after the app's own routes are already registered at import
    # time, so /health, /auth/login, etc. (whether served locally or
    # proxied) still win — this / mount only catches what no route matched
    # (the SPA's static assets).
    fastapi_app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    threading.Thread(target=_run_server, args=(fastapi_app,), daemon=True).start()
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
