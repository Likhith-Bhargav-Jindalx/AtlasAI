from fastapi.testclient import TestClient

from atlas_engine.api import app


def test_cors_allows_credentialed_requests_from_the_vite_dev_server():
    client = TestClient(app)
    response = client.get("/health", headers={"Origin": "http://localhost:5183"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5183"
    assert response.headers["access-control-allow-credentials"] == "true"
