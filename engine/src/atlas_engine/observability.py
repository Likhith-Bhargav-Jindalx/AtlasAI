import contextvars
import json
import logging
import os
import sys
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """The current request's id, set by RequestIdMiddleware — None outside
    a request (e.g. a CLI command)."""
    return _request_id_var.get()


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: int = logging.INFO) -> None:
    """Switches the root logger to structured JSON on stdout, for Container
    Apps' log pipeline to pick up with no extra plumbing. Safe to call more
    than once — always ends with exactly one stdout handler (plus one file
    handler, if ATLAS_LOG_FILE is set).

    A double-clicked desktop .app has no terminal attached, so stdout goes
    nowhere the person running it can ever see — every traceback the global
    exception handler already logs (unhandled_exception_handler in api.py)
    was previously unrecoverable for that failure mode. desktop/app.py sets
    ATLAS_LOG_FILE to a fixed path before importing this module's caller,
    so a real desktop-app crash leaves a file behind to read afterward."""
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(_JsonFormatter())
    root.addHandler(stdout_handler)

    log_file = os.environ.get("ATLAS_LOG_FILE")
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=3)
        file_handler.setFormatter(_JsonFormatter())
        root.addHandler(file_handler)

    # The Azure SDK's HTTP logging policy logs full request/response
    # headers at INFO for every call -- including one pair per ~4MB chunk
    # of a large blob upload (video/trace/network.har), which drowns out
    # everything else at INFO level. WARNING+ still surfaces real problems.
    logging.getLogger("azure").setLevel(logging.WARNING)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assigns a fresh request id to every inbound request, exposed to
    application code via get_request_id() and echoed back as the
    X-Request-Id response header, so one agent's session can be traced
    end-to-end in Log Analytics."""

    async def dispatch(self, request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        token = _request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            _request_id_var.reset(token)
        response.headers["X-Request-Id"] = request_id
        return response
