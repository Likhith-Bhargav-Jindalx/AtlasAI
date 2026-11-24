import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.auth.passwords import hash_password
from atlas_engine.db import session_scope
from atlas_engine.db.models import FormOption, Organization, User


def _make_user(*, role: str = "member", job_function: str | None = "developer") -> tuple[str, TestClient]:
    with session_scope() as session:
        org = Organization(name="Form Options API Org", slug=f"form-options-api-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        user = User(
            org_id=org.id, email=f"{uuid.uuid4()}@example.com", password_hash=hash_password("pw"),
            role=role, job_function=job_function,
        )
        session.add(user)
        session.flush()
        email = user.email

    client = TestClient(app)
    login = client.post("/auth/login", json={"email": email, "password": "pw"})
    assert login.status_code == 200
    return login.json()["org_id"], client


def test_get_form_options_returns_only_active_rows_for_any_authenticated_user():
    org_id, client = _make_user(role="member")
    with session_scope() as session:
        session.add_all([
            FormOption(org_id=uuid.UUID(org_id), lob="AR", category="action", label="Active One", active=True),
            FormOption(org_id=uuid.UUID(org_id), lob="AR", category="action", label="Inactive One", active=False),
        ])

    response = client.get("/form-options?lob=AR&category=action")
    assert response.status_code == 200
    assert [o["label"] for o in response.json()] == ["Active One"]


def test_get_form_options_401_without_a_session():
    client = TestClient(app)
    response = client.get("/form-options?lob=AR&category=action")
    assert response.status_code == 401


def test_get_form_options_all_requires_owner_or_admin():
    _, member_client = _make_user(role="member", job_function="developer")
    response = member_client.get("/form-options/all?lob=AR&category=action")
    assert response.status_code == 403

    _, owner_client = _make_user(role="owner", job_function="developer")
    response = owner_client.get("/form-options/all?lob=AR&category=action")
    assert response.status_code == 200


def test_post_form_options_requires_owner_or_admin_and_creates():
    _, member_client = _make_user(role="member", job_function="developer")
    response = member_client.post("/form-options", json={"lob": "AR", "category": "action", "label": "New One"})
    assert response.status_code == 403

    _, admin_client = _make_user(role="admin", job_function="operations_agent")
    response = admin_client.post("/form-options", json={"lob": "AR", "category": "action", "label": "New One"})
    assert response.status_code == 200
    body = response.json()
    assert body["label"] == "New One"
    assert body["active"] is True


def test_post_form_options_409_on_duplicate():
    _, owner_client = _make_user(role="owner")
    first = owner_client.post("/form-options", json={"lob": "AR", "category": "action", "label": "Dup"})
    assert first.status_code == 200
    second = owner_client.post("/form-options", json={"lob": "AR", "category": "action", "label": "Dup"})
    assert second.status_code == 409


def test_patch_form_options_requires_owner_or_admin_and_deactivates():
    org_id, owner_client = _make_user(role="owner")
    created = owner_client.post("/form-options", json={"lob": "AR", "category": "action", "label": "To Deactivate"})
    option_id = created.json()["id"]

    _, member_client = _make_user(role="member", job_function="developer")
    response = member_client.patch(f"/form-options/{option_id}", json={"active": False})
    assert response.status_code == 403

    response = owner_client.patch(f"/form-options/{option_id}", json={"active": False})
    assert response.status_code == 200
    assert response.json()["active"] is False

    listed = owner_client.get("/form-options?lob=AR&category=action")
    assert "To Deactivate" not in [o["label"] for o in listed.json()]


def test_post_form_options_allows_a_member_with_an_explicit_grant():
    org_id, member_client = _make_user(role="member", job_function="developer")
    with session_scope() as session:
        user = session.query(User).filter_by(org_id=uuid.UUID(org_id)).one()
        user.can_manage_form_options = True

    response = member_client.post("/form-options", json={"lob": "AR", "category": "action", "label": "Granted Create"})
    assert response.status_code == 200


def test_get_form_options_all_allows_a_member_with_an_explicit_grant():
    org_id, member_client = _make_user(role="member", job_function="developer")
    with session_scope() as session:
        user = session.query(User).filter_by(org_id=uuid.UUID(org_id)).one()
        user.can_manage_form_options = True

    response = member_client.get("/form-options/all?lob=AR&category=action")
    assert response.status_code == 200


def test_post_form_options_still_403_for_a_member_without_a_grant():
    _, member_client = _make_user(role="member", job_function="developer")
    response = member_client.post("/form-options", json={"lob": "AR", "category": "action", "label": "Ungranted"})
    assert response.status_code == 403
