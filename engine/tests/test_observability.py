import logging

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.observability import _JsonFormatter, configure_logging, get_request_id


def test_request_id_header_is_set_and_unique_per_request():
    client = TestClient(app)
    r1 = client.get("/health")
    r2 = client.get("/health")
    assert r1.headers["x-request-id"]
    assert r2.headers["x-request-id"]
    assert r1.headers["x-request-id"] != r2.headers["x-request-id"]


def test_request_id_header_is_echoed_back_when_client_supplies_one():
    client = TestClient(app)
    response = client.get("/health", headers={"X-Request-Id": "test-fixed-id"})
    assert response.headers["x-request-id"] == "test-fixed-id"


def test_json_formatter_includes_request_id_and_message_fields():
    record = logging.LogRecord("atlas_engine.test", logging.INFO, __file__, 1, "hello", None, None)
    formatted = _JsonFormatter().format(record)
    assert '"message": "hello"' in formatted
    assert '"request_id"' in formatted


def test_get_request_id_is_none_outside_a_request():
    assert get_request_id() is None


def test_configure_logging_writes_to_a_file_when_atlas_log_file_is_set(tmp_path, monkeypatch):
    """A double-clicked desktop .app has no terminal attached -- stdout
    goes nowhere anyone can read, so a real crash (like an unhandled
    exception inside the live-capture background thread) is otherwise
    unrecoverable. desktop/app.py sets ATLAS_LOG_FILE before importing
    atlas_engine.api to make this durable."""
    log_path = tmp_path / "nested" / "atlas-desktop.log"
    monkeypatch.setenv("ATLAS_LOG_FILE", str(log_path))
    try:
        configure_logging()
        logging.getLogger("atlas_engine.test").info("hello from the log file test")
        for handler in logging.getLogger().handlers:
            handler.flush()

        assert log_path.exists()
        assert "hello from the log file test" in log_path.read_text()
    finally:
        monkeypatch.delenv("ATLAS_LOG_FILE", raising=False)
        configure_logging()  # restore the default (stdout-only) handler set for later tests
