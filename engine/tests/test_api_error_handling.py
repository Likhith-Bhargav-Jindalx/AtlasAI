from fastapi.testclient import TestClient

from atlas_engine.api import app


def test_unhandled_exception_returns_clean_json_with_matching_request_id():
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/author",
        json={"org_id": "not-a-uuid", "start_url": "http://example.com", "actions": []},
    )

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "internal_server_error"
    assert body["request_id"] == response.headers["x-request-id"]
    assert "Traceback" not in response.text
    assert "ValueError" not in response.text
