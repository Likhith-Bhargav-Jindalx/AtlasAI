import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.auth.passwords import hash_password
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, User
from atlas_engine.llm.usage import record_llm_usage


def test_llm_usage_summary_requires_developer_job_function():
    with session_scope() as session:
        org = Organization(name="Usage API Org", slug=f"usage-api-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        agent = User(org_id=org.id, email=f"{uuid.uuid4()}@example.com", password_hash=hash_password("pw"), job_function="operations_agent")
        session.add(agent)
        session.flush()
        email = agent.email

    client = TestClient(app)
    client.post("/auth/login", json={"email": email, "password": "pw"})
    response = client.get(f"/llm-usage/summary?org_id={org_id}")
    assert response.status_code == 403


def test_llm_usage_summary_returns_totals_for_a_developer():
    with session_scope() as session:
        org = Organization(name="Usage API Dev Org", slug=f"usage-api-dev-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        dev = User(org_id=org.id, email=f"{uuid.uuid4()}@example.com", password_hash=hash_password("pw"), job_function="developer")
        session.add(dev)
        session.flush()
        email = dev.email

    record_llm_usage(org_id, None, model="gpt-4o", input_tokens=10, output_tokens=10)

    client = TestClient(app)
    client.post("/auth/login", json={"email": email, "password": "pw"})
    response = client.get(f"/llm-usage/summary?org_id={org_id}")
    assert response.status_code == 200
    assert response.json()["total_calls"] == 1
