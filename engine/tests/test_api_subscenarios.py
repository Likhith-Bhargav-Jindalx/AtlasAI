import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.auth.passwords import hash_password
from atlas_engine.db import session_scope
from atlas_engine.db.models import FormOption, Organization, Subscenario, User


def _make_user(*, role: str = "member", job_function: str | None = "developer") -> tuple[str, TestClient]:
    with session_scope() as session:
        org = Organization(name="Subscenarios API Org", slug=f"subscenarios-api-org-{uuid.uuid4()}")
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


def _make_action(org_id: str, label: str = "Denial Correction") -> str:
    with session_scope() as session:
        action = FormOption(org_id=uuid.UUID(org_id), lob="AR", category="action", label=label)
        session.add(action)
        session.flush()
        return str(action.id)


def test_get_subscenarios_returns_only_active_rows_for_any_authenticated_user():
    org_id, client = _make_user(role="member")
    action_id = _make_action(org_id)
    with session_scope() as session:
        session.add_all([
            Subscenario(action_id=uuid.UUID(action_id), label="Active One", active=True),
            Subscenario(action_id=uuid.UUID(action_id), label="Inactive One", active=False),
        ])

    response = client.get(f"/subscenarios?action_id={action_id}")
    assert response.status_code == 200
    assert [s["label"] for s in response.json()] == ["Active One"]


def test_get_subscenarios_401_without_a_session():
    client = TestClient(app)
    response = client.get(f"/subscenarios?action_id={uuid.uuid4()}")
    assert response.status_code == 401


def test_get_subscenarios_all_requires_owner_admin_or_grant():
    org_id, member_client = _make_user(role="member", job_function="developer")
    action_id = _make_action(org_id)
    response = member_client.get(f"/subscenarios/all?action_id={action_id}")
    assert response.status_code == 403

    owner_org_id, owner_client = _make_user(role="owner", job_function="developer")
    owner_action_id = _make_action(owner_org_id)
    response = owner_client.get(f"/subscenarios/all?action_id={owner_action_id}")
    assert response.status_code == 200


def test_post_subscenarios_requires_owner_admin_or_grant_and_creates():
    org_id, member_client = _make_user(role="member", job_function="developer")
    action_id = _make_action(org_id)
    response = member_client.post("/subscenarios", json={"action_id": action_id, "label": "New One"})
    assert response.status_code == 403

    admin_org_id, admin_client = _make_user(role="admin", job_function="operations_agent")
    admin_action_id = _make_action(admin_org_id)
    response = admin_client.post("/subscenarios", json={"action_id": admin_action_id, "label": "New One"})
    assert response.status_code == 200
    body = response.json()
    assert body["label"] == "New One"
    assert body["active"] is True


def test_post_subscenarios_400_on_non_action_form_option():
    org_id, owner_client = _make_user(role="owner")
    with session_scope() as session:
        client_option = FormOption(org_id=uuid.UUID(org_id), lob="AR", category="client", label="Acme Clinic")
        session.add(client_option)
        session.flush()
        client_option_id = str(client_option.id)

    response = owner_client.post("/subscenarios", json={"action_id": client_option_id, "label": "Bad"})
    assert response.status_code == 400


def test_post_subscenarios_409_on_duplicate():
    org_id, owner_client = _make_user(role="owner")
    action_id = _make_action(org_id)
    first = owner_client.post("/subscenarios", json={"action_id": action_id, "label": "Dup"})
    assert first.status_code == 200
    second = owner_client.post("/subscenarios", json={"action_id": action_id, "label": "Dup"})
    assert second.status_code == 409


def test_patch_subscenarios_requires_owner_admin_or_grant_and_deactivates():
    org_id, owner_client = _make_user(role="owner")
    action_id = _make_action(org_id)
    created = owner_client.post("/subscenarios", json={"action_id": action_id, "label": "To Deactivate"})
    subscenario_id = created.json()["id"]

    _, member_client = _make_user(role="member", job_function="developer")
    response = member_client.patch(f"/subscenarios/{subscenario_id}", json={"active": False})
    assert response.status_code == 403

    response = owner_client.patch(f"/subscenarios/{subscenario_id}", json={"active": False})
    assert response.status_code == 200
    assert response.json()["active"] is False

    listed = owner_client.get(f"/subscenarios?action_id={action_id}")
    assert "To Deactivate" not in [s["label"] for s in listed.json()]


def test_post_subscenarios_allows_a_member_with_an_explicit_grant():
    org_id, member_client = _make_user(role="member", job_function="developer")
    action_id = _make_action(org_id)
    with session_scope() as session:
        user = session.query(User).filter_by(org_id=uuid.UUID(org_id)).one()
        user.can_manage_form_options = True

    response = member_client.post("/subscenarios", json={"action_id": action_id, "label": "Granted Create"})
    assert response.status_code == 200
