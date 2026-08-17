"""Desktop shell entrypoint: runs the atlas_engine FastAPI app locally and
shows the built frontend in a native pywebview window instead of a browser
tab. Targets local Postgres/storage, exactly like `npm run dev` + `uvicorn`
do today — pointing this at a shared cloud backend instead is a config
change for later, not more packaging work.
"""

import os
import sys
import threading
import time
from pathlib import Path

import uvicorn
import webview
from fastapi.staticfiles import StaticFiles

from atlas_engine.api import app as fastapi_app

PORT = int(os.environ.get("ATLAS_DESKTOP_PORT", "8000"))


def _resource_path(relative: str) -> Path:
    """Resolves a bundled resource both when run unfrozen (dev testing,
    relative to this file) and when frozen by PyInstaller (relative to
    sys._MEIPASS, the bundle's extraction dir)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def _run_server() -> None:
    uvicorn.run(fastapi_app, host="127.0.0.1", port=PORT, log_level="info")


def main() -> None:
    frontend_dist = _resource_path("frontend_dist")

    # Mounted after atlas_engine.api's own routes are already registered at
    # import time, so /health, /auth/login, etc. still win — this / mount
    # only catches what no API route matched (the SPA's static assets).
    fastapi_app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    threading.Thread(target=_run_server, daemon=True).start()
    time.sleep(1.5)

    webview.create_window("Atlas AI", f"http://127.0.0.1:{PORT}/", width=1280, height=800)
    webview.start()


if __name__ == "__main__":
    main()
