import contextvars
import json
import logging
import sys
import uuid

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
    than once — always ends with exactly one handler."""
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)

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
