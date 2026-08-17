import logging

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.observability import _JsonFormatter, get_request_id


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
