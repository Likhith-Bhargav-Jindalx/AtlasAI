import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.auth.passwords import hash_password
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, User


def _make_user(email: str, password: str) -> None:
    with session_scope() as session:
        org = Organization(name="Auth API Org", slug=f"auth-api-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        session.add(
            User(org_id=org.id, email=email, password_hash=hash_password(password), role="member")
        )


def test_login_sets_cookie_and_me_returns_identity():
    email = f"login-test-{uuid.uuid4()}@example.com"
    _make_user(email, "correct-password")

    client = TestClient(app)
    login_response = client.post("/auth/login", json={"email": email, "password": "correct-password"})

    assert login_response.status_code == 200
    assert login_response.json()["email"] == email
    assert "atlas_session" in login_response.cookies

    me_response = client.get("/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == email


def test_login_rejects_wrong_password():
    email = f"wrong-pw-{uuid.uuid4()}@example.com"
    _make_user(email, "correct-password")

    client = TestClient(app)
    response = client.post("/auth/login", json={"email": email, "password": "wrong-password"})

    assert response.status_code == 401


def test_login_rejects_unknown_email():
    client = TestClient(app)
    response = client.post("/auth/login", json={"email": "nobody@example.com", "password": "x"})

    assert response.status_code == 401


def test_me_without_a_session_returns_401():
    client = TestClient(app)
    response = client.get("/auth/me")

    assert response.status_code == 401


def test_logout_clears_the_session():
    email = f"logout-test-{uuid.uuid4()}@example.com"
    _make_user(email, "correct-password")

    client = TestClient(app)
    client.post("/auth/login", json={"email": email, "password": "correct-password"})

    logout_response = client.post("/auth/logout")
    assert logout_response.status_code == 200

    me_response = client.get("/auth/me")
    assert me_response.status_code == 401
