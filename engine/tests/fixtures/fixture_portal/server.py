"""Stdlib-only HTTP server serving the fixture portal — no extra
dependencies, so the test suite never needs network access to run."""

import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

_DIR = Path(__file__).parent


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(_DIR), **kwargs)

    def do_GET(self):
        if self.path == "/":
            self.path = "/login.html"
        if self.path == "/dashboard.html" and getattr(self.server, "drift_mode", False):
            self.path = "/dashboard_drifted.html"
        if self.path == "/report.csv":
            body = b"patient_id,report_status\n1,ready\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass  # silence per-request logging in test output


class FixturePortalServer:
    def __init__(self, port: int = 8731):
        self.port = port
        self._httpd = HTTPServer(("127.0.0.1", port), _Handler)
        self._httpd.drift_mode = False
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def set_drift_mode(self, enabled: bool) -> None:
        """When enabled, /dashboard.html serves the drifted variant — used
        to simulate a portal UI change for healing tests."""
        self._httpd.drift_mode = enabled

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
