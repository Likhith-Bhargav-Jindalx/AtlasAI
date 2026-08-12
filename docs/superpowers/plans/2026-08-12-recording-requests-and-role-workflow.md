# Recording Requests & Role-Based Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split "recording" from "authoring" into two independent services, and add a request/queue data model so an operations agent can submit a recording request and a developer can pick it up, author a golden script from it, and have the request marked fulfilled — while every existing Phase 0/1 command, endpoint, and test keeps working unmodified.

**Architecture:** Two new modules — `recording_service.py` (capture persistence, requests, the queue) and `authoring_service.py` (trajectory replay → brain → emit → certify) — extracted from what `cli.py`'s `author_from_recorded_actions` currently does inline. One new table (`recording_requests`) and three new columns (`recordings.request_id`, `recordings.role`, `users.job_function`). Per the design spec, `2026-08-12-recording-requests-and-role-workflow-design.md`.

**Tech Stack:** Same as Phase 0/1 — no new dependencies.

## Global Constraints

- Every existing Phase 0/1 CLI command, API endpoint, and test must keep passing unmodified — this plan is additive. `author_from_recorded_actions` keeps its exact name, location (`cli.py`), signature, and return shape.
- `authoring_service.author_from_recording` must work identically regardless of who made the recording or what `role` it's tagged with — this is what makes "author directly from the ops agent's recording" a zero-code-change future capability, not something to build later.
- A `RecordingRequest` only becomes `fulfilled` when a `certified` golden script references (via its recording) that request — a `draft` result never fulfills a request.
- `claimed_by` is advisory, not a lock — no code in this plan prevents two people from recording against the same open request.
- Every step ends with tests passing and a commit. Do not proceed to the next task with failing tests.

---

## File Structure

```
engine/src/atlas_engine/
├── db/models/
│   ├── requests.py           # NEW — RecordingRequest
│   ├── identity.py           # MODIFIED — User gains job_function
│   ├── portal.py             # MODIFIED — Recording gains request_id, role
│   └── __init__.py           # MODIFIED — export RecordingRequest
├── recording_service.py      # NEW — persist_recording, create/list/claim_recording_request
├── authoring_service.py      # NEW — author_from_recording
├── cli.py                    # MODIFIED — author_from_recorded_actions becomes a thin wrapper;
│                              #   record gains --request-id/--role; new commands added
└── api.py                    # MODIFIED — new endpoints

engine/migrations/versions/
└── <new>_add_recording_requests.py   # NEW

engine/tests/
├── test_db_models_requests.py        # NEW
├── test_recording_service.py         # NEW
├── test_authoring_service.py         # NEW
├── test_cli_requests.py              # NEW
├── test_api_requests.py              # NEW
└── test_end_to_end_requests.py       # NEW — the capstone
```

---

### Task 1: Data model — `RecordingRequest`, and new columns on `Recording`/`User`

**Files:**
- Create: `engine/src/atlas_engine/db/models/requests.py`
- Modify: `engine/src/atlas_engine/db/models/identity.py` (`User`)
- Modify: `engine/src/atlas_engine/db/models/portal.py` (`Recording`)
- Modify: `engine/src/atlas_engine/db/models/__init__.py`
- Create: `engine/migrations/versions/<new>_add_recording_requests.py`
- Create: `engine/tests/test_db_models_requests.py`

**Interfaces:**
- Produces: `RecordingRequest` (fields per the design spec §3.1) with relationships `org`, `requester`, `claimant`, `portal_profile`, `fulfilled_golden_script`; `Recording.request_id` / `Recording.role` (`"demo"` default); `User.job_function` (nullable, `"operations_agent"` | `"developer"`).

- [ ] **Step 1: Write the failing test**

`engine/tests/test_db_models_requests.py`:
```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas_engine.db import Base
from atlas_engine.db.models import (
    GoldenScript,
    Organization,
    PortalProfile,
    Recording,
    RecordingRequest,
    User,
)


def _sqlite_engine():
    return create_engine("sqlite:///:memory:")


def test_recording_request_links_org_user_and_portal_profile():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Requests Org", slug="requests-org")
        agent = User(
            org=org, email="agent@example.com", password_hash="x", role="member",
            job_function="operations_agent",
        )
        profile = PortalProfile(org=org, domain="portal.example.com")
        request = RecordingRequest(
            org=org, requester=agent, portal_profile=profile,
            target_url="https://portal.example.com/login", title="Check eligibility",
        )
        session.add_all([org, agent, profile, request])
        session.commit()

        assert request.id is not None
        assert request.status == "open"
        assert request.requester.email == "agent@example.com"


def test_recording_links_to_request_with_role():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Requests Org 2", slug="requests-org-2")
        agent = User(org=org, email="agent2@example.com", password_hash="x", role="member")
        profile = PortalProfile(org=org, domain="portal2.example.com")
        request = RecordingRequest(
            org=org, requester=agent, portal_profile=profile,
            target_url="https://portal2.example.com", title="X",
        )
        session.add_all([org, agent, profile, request])
        session.flush()

        recording = Recording(
            org=org, portal_profile=profile, status="captured",
            capture_blob_prefix="orgs/x/recordings/1/", request_id=request.id, role="demo",
        )
        session.add(recording)
        session.commit()

        assert recording.role == "demo"
        assert recording.request_id == request.id


def test_recording_request_fulfilled_golden_script_reference():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Requests Org 3", slug="requests-org-3")
        agent = User(org=org, email="agent3@example.com", password_hash="x", role="member")
        profile = PortalProfile(org=org, domain="portal3.example.com")
        request = RecordingRequest(
            org=org, requester=agent, portal_profile=profile,
            target_url="https://portal3.example.com", title="X",
        )
        session.add_all([org, agent, profile, request])
        session.flush()

        golden = GoldenScript(org=org, portal_profile=profile, version=1, script_blob_ref="v1.py", status="certified")
        session.add(golden)
        session.flush()

        request.status = "fulfilled"
        request.fulfilled_golden_script_id = golden.id
        session.commit()

        assert request.fulfilled_golden_script.id == golden.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db_models_requests.py -v`
Expected: `FAIL` — `ImportError: cannot import name 'RecordingRequest'`.

- [ ] **Step 3: Write `engine/src/atlas_engine/db/models/requests.py`**

```python
import uuid
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atlas_engine.db import Base

RECORDING_REQUEST_STATUSES = ("open", "submitted", "fulfilled", "cancelled")


class RecordingRequest(Base):
    __tablename__ = "recording_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    portal_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("portal_profiles.id", ondelete="CASCADE"))
    target_url: Mapped[str]
    title: Mapped[str]
    description: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(
        Enum(*RECORDING_REQUEST_STATUSES, name="recording_request_status"), default="open"
    )
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    claimed_at: Mapped[datetime | None] = mapped_column(default=None)
    fulfilled_golden_script_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("golden_scripts.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    org: Mapped["Organization"] = relationship()
    requester: Mapped["User"] = relationship(foreign_keys=[requested_by])
    claimant: Mapped["User | None"] = relationship(foreign_keys=[claimed_by])
    portal_profile: Mapped["PortalProfile"] = relationship()
    fulfilled_golden_script: Mapped["GoldenScript | None"] = relationship(foreign_keys=[fulfilled_golden_script_id])
```

- [ ] **Step 4: Modify `engine/src/atlas_engine/db/models/identity.py`** — add `job_function` to `User`

Add near the top, alongside `USER_ROLES`:
```python
JOB_FUNCTIONS = ("operations_agent", "developer")
```

Add this column to the `User` class, after `role`:
```python
    job_function: Mapped[str | None] = mapped_column(Enum(*JOB_FUNCTIONS, name="job_function"), default=None)
```

- [ ] **Step 5: Modify `engine/src/atlas_engine/db/models/portal.py`** — add `request_id` and `role` to `Recording`

Add near the top, alongside `RECORDING_STATUSES`:
```python
RECORDING_ROLES = ("demo", "authoring")
```

Add these two columns to the `Recording` class (after `user_id`):
```python
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recording_requests.id", ondelete="SET NULL"), default=None
    )
    role: Mapped[str] = mapped_column(Enum(*RECORDING_ROLES, name="recording_role"), default="demo")
```

- [ ] **Step 6: Update `engine/src/atlas_engine/db/models/__init__.py`**

Add the import and `__all__` entry:
```python
from atlas_engine.db.models.requests import RecordingRequest
```
Add `"RecordingRequest"` to `__all__`.

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_db_models_requests.py -v`
Expected: `PASS` on all three tests.

- [ ] **Step 8: Generate and apply the Alembic migration**

Run:
```bash
cd engine
alembic revision --autogenerate -m "add recording requests and role-based workflow"
```

This migration is purely additive on top of an already-applied schema (no circular dependency this time — `recording_requests` depends only on already-existing tables, and the new `recordings.request_id` column depends on the newly-created `recording_requests`), so autogenerate should produce a working, correctly-ordered migration. Open the generated file and verify:
1. `recording_requests` is created before `recordings` gets its `request_id` column added.
2. The `role` column on `recordings` and `job_function` on `users` are added with a `server_default` (e.g. `server_default='demo'` for `role`) — both tables may already have rows from Phase 0/1 test runs, and a `NOT NULL` column added without a default will fail against existing rows. `job_function` is nullable so it needs no default; `role` is `NOT NULL` and does.
3. `downgrade()` drops the new foreign key constraint before dropping `recording_requests`, and drops the three new Postgres enum types (`recording_request_status`, `recording_role`, `job_function`) via `op.execute('DROP TYPE IF EXISTS ...')` — the same fix Phase 0's initial migration needed, for the same reason (`sa.Enum` on `create_table`/`add_column` creates a standalone type that `drop_table`/`drop_column` doesn't remove).

Fix anything that doesn't match the above, following the same approach used for the initial schema migration.

- [ ] **Step 9: Apply to both databases**

Run:
```bash
alembic upgrade head
ATLAS_DATABASE_URL="postgresql+psycopg://atlas:atlas_dev_password@localhost:5434/atlas_test" alembic upgrade head
```

Verify: `docker compose -f ../infra/docker-compose.dev.yml exec postgres psql -U atlas -d atlas -c "\dt"` shows `recording_requests` alongside the existing 14 tables.

- [ ] **Step 10: Run the full suite**

Run: `pytest -v`
Expected: `PASS` on everything — the new model tests plus all of Phase 0/1's existing tests, unaffected by additive schema changes.

- [ ] **Step 11: Commit**

```bash
git add engine/src/atlas_engine/db/models engine/migrations engine/tests/test_db_models_requests.py
git commit -m "feat: add RecordingRequest model and role/request columns on Recording/User"
```

---

### Task 2: `recording_service.persist_recording` — the shared, canonical recording-persistence function

**Files:**
- Create: `engine/src/atlas_engine/recording_service.py`
- Create: `engine/tests/test_recording_service.py`

**Interfaces:**
- Consumes: `extract_trajectory` (Phase 0), `trajectory_to_dict` (Phase 1), `infer_portal_profile` (Phase 0), `get_or_create_portal_profile` (Phase 1), `LocalRunStore` (Phase 0).
- Produces: `persist_recording(org_id: str, start_url: str, actions: list[RecordedAction], *, request_id: uuid.UUID | None = None, role: str = "demo") -> uuid.UUID`. Persists `actions.json` + `trajectory.json` to a new run directory, creates (or reuses, via get-or-create) the `PortalProfile`, creates the `Recording` and `ReferenceTrajectory` rows, and — if `request_id` is given and that request is currently `"open"` — advances it to `"submitted"`. Returns the new recording's id.

This function **replaces** the recording-persistence half of today's `author_from_recorded_actions` (`cli.py`) *and* the cruder, duplicate portal-profile logic in today's `record` CLI command — unifying them onto the smarter `infer_portal_profile` heuristic (which also captures a `success_signal`, unlike the plain "use `start_url` as the login URL" logic `author_from_recorded_actions` currently has) and onto `get_or_create_portal_profile` (fixing a latent bug: today's `record` command creates a brand-new `PortalProfile` on every call with no get-or-create check, which would raise a unique-constraint violation the second time anyone records against the same domain).

- [ ] **Step 1: Write the failing test**

`engine/tests/test_recording_service.py`:
```python
import json
import uuid
from pathlib import Path

from atlas_engine.capture.events import RecordedAction
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, PortalProfile, Recording, ReferenceTrajectory
from atlas_engine.recording_service import persist_recording


def test_persist_recording_creates_recording_and_trajectory(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Persist Service Org", slug=f"persist-service-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    actions = [
        RecordedAction(0, "fill", [{"selector": "#username", "rank": 0}], "demo", f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/login.html", "", ""),
        RecordedAction(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/login.html", "", ""),
        RecordedAction(2, "click", [{"selector": "#login-btn", "rank": 0}], None, f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/dashboard.html", "", ""),
    ]

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", actions)

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        assert recording.status == "captured"
        assert recording.role == "demo"
        assert recording.request_id is None

        trajectory_row = session.query(ReferenceTrajectory).filter_by(recording_id=recording_id).one()
        data = json.loads(Path(trajectory_row.trajectory_blob_ref).read_text())
        assert len(data["steps"]) == 3

        profile = session.get(PortalProfile, recording.portal_profile_id)
        assert profile.inferred_login_url == f"{fixture_portal_url}/login.html"
        assert profile.inferred_success_signal == {"url_contains": "/dashboard.html"}


def test_persist_recording_reuses_portal_profile_across_two_recordings(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Reuse Profile Org", slug=f"reuse-profile-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    actions = [
        RecordedAction(0, "click", [{"selector": "#login-btn", "rank": 0}], None, f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/dashboard.html", "", ""),
    ]

    recording_id_1 = persist_recording(org_id, f"{fixture_portal_url}/login.html", actions)
    recording_id_2 = persist_recording(org_id, f"{fixture_portal_url}/login.html", actions)

    with session_scope() as session:
        profile_1 = session.get(Recording, recording_id_1).portal_profile_id
        profile_2 = session.get(Recording, recording_id_2).portal_profile_id
        assert profile_1 == profile_2


def test_persist_recording_advances_open_request_to_submitted(fixture_portal_url):
    from atlas_engine.db.models import RecordingRequest
    from atlas_engine.db.repo import get_or_create_portal_profile

    with session_scope() as session:
        org = Organization(name="Request Advance Org", slug=f"request-advance-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        agent = __import__("atlas_engine.db.models", fromlist=["User"]).User(
            org_id=org.id, email="agent@example.com", password_hash="x", role="member"
        )
        session.add(agent)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), fixture_portal_url.split("//")[1])
        request = RecordingRequest(
            org_id=org.id, requested_by=agent.id, portal_profile_id=profile.id,
            target_url=f"{fixture_portal_url}/login.html", title="Test request",
        )
        session.add(request)
        session.flush()
        org_id = str(org.id)
        request_id = request.id

    actions = [
        RecordedAction(0, "click", [{"selector": "#login-btn", "rank": 0}], None, f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/dashboard.html", "", ""),
    ]

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", actions, request_id=request_id, role="demo")

    with session_scope() as session:
        assert session.get(Recording, recording_id).request_id == request_id
        assert session.get(RecordingRequest, request_id).status == "submitted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_recording_service.py -v`
Expected: `FAIL` with `ModuleNotFoundError: No module named 'atlas_engine.recording_service'`.

- [ ] **Step 3: Write `engine/src/atlas_engine/recording_service.py`**

```python
import json
import uuid
from datetime import datetime, timezone

from atlas_engine.brain.serialize import trajectory_to_dict
from atlas_engine.capture.events import RecordedAction
from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import Recording, RecordingRequest, ReferenceTrajectory
from atlas_engine.db.repo import get_or_create_portal_profile
from atlas_engine.portal_profile.infer import infer_portal_profile
from atlas_engine.storage import LocalRunStore
from atlas_engine.trajectory.extract import extract_trajectory


def persist_recording(
    org_id: str,
    start_url: str,
    actions: list[RecordedAction],
    *,
    request_id: uuid.UUID | None = None,
    role: str = "demo",
) -> uuid.UUID:
    """Persists a captured recording — actions.json, the extracted
    trajectory, and the Recording/ReferenceTrajectory rows — without
    running any authoring. This is the recording module's one entrypoint;
    authoring_service.author_from_recording is the only thing that
    consumes what this produces.
    """
    store = LocalRunStore(root=settings.ensure_run_root())
    run_dir = store.new_run_dir(prefix="recordings")
    run_dir.write_json("actions.json", [vars(a) for a in actions])

    trajectory = extract_trajectory(actions)
    trajectory_path = run_dir.path / "trajectory.json"
    trajectory_path.write_text(json.dumps(trajectory_to_dict(trajectory), indent=2))

    profile_data = infer_portal_profile(start_url, actions)

    with session_scope() as session:
        profile = get_or_create_portal_profile(session, org_id, profile_data.domain)
        if profile.inferred_login_url is None:
            profile.inferred_login_url = profile_data.login_url
        if profile.inferred_success_signal is None and profile_data.success_signal is not None:
            profile.inferred_success_signal = profile_data.success_signal
        if profile.confidence_score is None and profile_data.confidence is not None:
            profile.confidence_score = profile_data.confidence

        recording = Recording(
            org_id=uuid.UUID(org_id),
            portal_profile_id=profile.id,
            status="captured",
            capture_blob_prefix=run_dir.blob_ref,
            request_id=request_id,
            role=role,
        )
        session.add(recording)
        session.flush()

        session.add(
            ReferenceTrajectory(
                recording_id=recording.id,
                trajectory_blob_ref=str(trajectory_path),
                step_count=len(trajectory.steps),
            )
        )

        if request_id is not None:
            request = session.get(RecordingRequest, request_id)
            if request is not None and request.status == "open":
                request.status = "submitted"

        recording_id = recording.id

    return recording_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_recording_service.py -v`
Expected: `PASS` on all three tests.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: `PASS` on everything — this task adds a new module and doesn't yet touch `cli.py`, so nothing existing should be affected.

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/recording_service.py engine/tests/test_recording_service.py
git commit -m "feat: add recording_service.persist_recording as the canonical recording-persistence path"
```

---

### Task 3: `recording_service` — create / list / claim recording requests

**Files:**
- Modify: `engine/src/atlas_engine/recording_service.py`
- Modify: `engine/tests/test_recording_service.py`

**Interfaces:**
- Produces: `create_recording_request(org_id: str, requested_by: str, target_url: str, title: str, description: str | None = None) -> uuid.UUID`; `list_recording_requests(org_id: str, status: str | None = None) -> list[dict]` (each dict: `id`, `title`, `target_url`, `status`, `claimed_by`); `claim_recording_request(request_id: uuid.UUID, developer_user_id: uuid.UUID) -> None`.

- [ ] **Step 1: Write the failing test** — append to `engine/tests/test_recording_service.py`

```python
def test_create_list_and_claim_recording_request(fixture_portal_url):
    from atlas_engine.db.models import RecordingRequest, User
    from atlas_engine.recording_service import (
        claim_recording_request,
        create_recording_request,
        list_recording_requests,
    )

    with session_scope() as session:
        org = Organization(name="Request Flow Org", slug=f"request-flow-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        agent = User(org_id=org.id, email="agent@example.com", password_hash="x", role="member")
        developer = User(org_id=org.id, email="dev@example.com", password_hash="x", role="member")
        session.add_all([agent, developer])
        session.flush()
        org_id = str(org.id)
        agent_id = str(agent.id)
        developer_id = developer.id

    request_id = create_recording_request(
        org_id, agent_id, f"{fixture_portal_url}/login.html", "Check eligibility", "See the demo recording"
    )

    open_requests = list_recording_requests(org_id, status="open")
    assert any(r["id"] == str(request_id) for r in open_requests)
    assert all(r["claimed_by"] is None for r in open_requests if r["id"] == str(request_id))

    claim_recording_request(request_id, developer_id)

    with session_scope() as session:
        request = session.get(RecordingRequest, request_id)
        assert request.claimed_by == developer_id
        assert request.claimed_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_recording_service.py::test_create_list_and_claim_recording_request -v`
Expected: `FAIL` with `ImportError` (the three functions don't exist yet).

- [ ] **Step 3: Append to `engine/src/atlas_engine/recording_service.py`**

Add this import at the top:
```python
from atlas_engine.db.models import PortalProfile
```

Append:
```python
def create_recording_request(
    org_id: str, requested_by: str, target_url: str, title: str, description: str | None = None
) -> uuid.UUID:
    domain = target_url.split("/")[2]
    with session_scope() as session:
        profile = get_or_create_portal_profile(session, org_id, domain)
        request = RecordingRequest(
            org_id=uuid.UUID(org_id),
            requested_by=uuid.UUID(requested_by),
            portal_profile_id=profile.id,
            target_url=target_url,
            title=title,
            description=description,
        )
        session.add(request)
        session.flush()
        return request.id


def list_recording_requests(org_id: str, status: str | None = None) -> list[dict]:
    with session_scope() as session:
        query = session.query(RecordingRequest).filter_by(org_id=uuid.UUID(org_id))
        if status is not None:
            query = query.filter_by(status=status)
        return [
            {
                "id": str(r.id),
                "title": r.title,
                "target_url": r.target_url,
                "status": r.status,
                "claimed_by": str(r.claimed_by) if r.claimed_by else None,
            }
            for r in query.order_by(RecordingRequest.created_at.desc()).all()
        ]


def claim_recording_request(request_id: uuid.UUID, developer_user_id: uuid.UUID) -> None:
    with session_scope() as session:
        request = session.get(RecordingRequest, request_id)
        if request is None:
            raise ValueError(f"No such recording request: {request_id}")
        request.claimed_by = developer_user_id
        request.claimed_at = datetime.now(timezone.utc).replace(tzinfo=None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_recording_service.py -v`
Expected: `PASS` on all four tests in the file.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: `PASS` on everything.

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/recording_service.py engine/tests/test_recording_service.py
git commit -m "feat: add create/list/claim recording request functions"
```

---

### Task 4: `authoring_service.author_from_recording` — the decoupled authoring entrypoint

**Files:**
- Create: `engine/src/atlas_engine/authoring_service.py`
- Create: `engine/tests/test_authoring_service.py`

**Interfaces:**
- Consumes: `BrainLoop` (Phase 0/1), `trajectory_from_dict` (Phase 1), `emit_golden_script`/`certify_golden_script` (Phase 0), `next_golden_script_version` (Phase 1).
- Produces: `author_from_recording(recording_id: uuid.UUID, *, llm: LlmClient | None = None) -> dict` — returns `{"status", "script_path", "golden_script_id"}`, the same shape `author_from_recorded_actions` already returns. Works on **any** recording regardless of `role` — this is the function that makes "author directly from the ops agent's demo recording" possible with zero further code changes. If the resulting script is `certified` and the recording has a `request_id`, the linked `RecordingRequest` is marked `fulfilled` with `fulfilled_golden_script_id` set.

- [ ] **Step 1: Write the failing test**

`engine/tests/test_authoring_service.py`:
```python
import uuid

from atlas_engine.authoring_service import author_from_recording
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, Organization, RecordingRequest, User
from atlas_engine.recording_service import create_recording_request, persist_recording


def _record_against_fixture(org_id: str, fixture_portal_url: str, request_id=None, role="demo") -> uuid.UUID:
    from playwright.sync_api import sync_playwright

    from atlas_engine.capture.recorder import CaptureSession

    start_url = f"{fixture_portal_url}/login.html"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(start_url)
        with CaptureSession(page) as capture:
            page.fill("#username", "demo")
            page.fill("#password", "demo-pass")
            page.click("#login-btn")
            page.wait_for_url("**/dashboard.html")
            page.click("#generate-report-btn")
        browser.close()

    return persist_recording(org_id, start_url, capture.actions, request_id=request_id, role=role)


def test_author_from_recording_produces_a_certified_script(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Authoring Service Org", slug=f"authoring-service-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = _record_against_fixture(org_id, fixture_portal_url)

    result = author_from_recording(recording_id)

    assert result["status"] == "certified"

    with session_scope() as session:
        golden = session.get(GoldenScript, uuid.UUID(result["golden_script_id"]))
        assert golden.created_from_recording_id == recording_id


def test_author_from_recording_works_directly_on_a_demo_role_recording(fixture_portal_url):
    """Proves the future path already works today: authoring doesn't care
    that this recording was tagged "demo" (an ops agent's walkthrough)
    rather than "authoring" (a developer's own) — no code change will be
    needed to skip the developer re-record step later."""
    with session_scope() as session:
        org = Organization(name="Direct Author Org", slug=f"direct-author-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = _record_against_fixture(org_id, fixture_portal_url, role="demo")

    result = author_from_recording(recording_id)

    assert result["status"] == "certified"


def test_author_from_recording_fulfills_a_linked_request_on_success(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Fulfill Org", slug=f"fulfill-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        agent = User(org_id=org.id, email="agent@example.com", password_hash="x", role="member")
        session.add(agent)
        session.flush()
        org_id = str(org.id)
        agent_id = str(agent.id)

    request_id = create_recording_request(org_id, agent_id, "http://placeholder/", "Fulfillment test")
    recording_id = _record_against_fixture(org_id, "http://127.0.0.1:8731", request_id=request_id, role="authoring")

    result = author_from_recording(recording_id)
    assert result["status"] == "certified"

    with session_scope() as session:
        request = session.get(RecordingRequest, request_id)
        assert request.status == "fulfilled"
        assert request.fulfilled_golden_script_id == uuid.UUID(result["golden_script_id"])
```

Note: `create_recording_request` is called with a placeholder `target_url` ("http://placeholder/") deliberately different from the fixture URL actually recorded against — this test is only checking the fulfillment linkage, not that the request's own URL matches; `create_recording_request` will resolve its own (unused) portal profile for that placeholder domain, independent of the one the actual recording uses. This is intentional and fine for this test's purpose.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_authoring_service.py -v`
Expected: `FAIL` with `ModuleNotFoundError: No module named 'atlas_engine.authoring_service'`.

- [ ] **Step 3: Write `engine/src/atlas_engine/authoring_service.py`**

```python
import json
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

from atlas_engine.brain.loop import BrainLoop
from atlas_engine.brain.serialize import trajectory_from_dict
from atlas_engine.codegen.emit import emit_golden_script
from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, PortalProfile, Recording, RecordingRequest
from atlas_engine.db.models import ReferenceTrajectory as ReferenceTrajectoryModel
from atlas_engine.db.repo import next_golden_script_version
from atlas_engine.llm.client import LlmClient
from atlas_engine.storage import LocalRunStore
from atlas_engine.verify.certify import certify_golden_script


def author_from_recording(recording_id: uuid.UUID, *, llm: LlmClient | None = None) -> dict:
    """Loads an already-persisted recording's trajectory and runs the
    authoring pipeline against it. Fully decoupled from recording_service:
    this works identically whether the recording was made by the person
    calling this function or by someone else entirely.
    """
    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        if recording is None:
            raise ValueError(f"No such recording: {recording_id}")

        profile = session.get(PortalProfile, recording.portal_profile_id)
        trajectory_row = (
            session.query(ReferenceTrajectoryModel).filter_by(recording_id=recording_id).one()
        )

        org_id = recording.org_id
        portal_profile_id = recording.portal_profile_id
        start_url = profile.inferred_login_url or f"https://{profile.domain}"
        trajectory_blob_ref = trajectory_row.trajectory_blob_ref
        request_id = recording.request_id

    trajectory = trajectory_from_dict(json.loads(Path(trajectory_blob_ref).read_text()))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(start_url)
        outcomes = BrainLoop(page, llm=llm).run(trajectory)
        browser.close()

    all_on_track = all(outcome.on_track for outcome in outcomes)

    store = LocalRunStore(root=settings.ensure_run_root())
    run_dir = store.new_run_dir(prefix="goldens")
    script_text = emit_golden_script(trajectory, start_url=start_url)
    script_path = run_dir.path / "golden.py"
    script_path.write_text(script_text)

    if not all_on_track:
        status = "draft"
    else:
        certification = certify_golden_script(script_path, runs=2)
        status = "certified" if certification.is_certified else "draft"

    with session_scope() as session:
        golden = GoldenScript(
            org_id=org_id,
            portal_profile_id=portal_profile_id,
            version=next_golden_script_version(session, portal_profile_id),
            script_blob_ref=str(script_path),
            status=status,
            created_from_recording_id=recording_id,
        )
        session.add(golden)
        session.flush()
        golden_script_id = golden.id

        if status == "certified" and request_id is not None:
            request = session.get(RecordingRequest, request_id)
            if request is not None:
                request.status = "fulfilled"
                request.fulfilled_golden_script_id = golden_script_id

    return {"status": status, "script_path": str(script_path), "golden_script_id": str(golden_script_id)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_authoring_service.py -v`
Expected: `PASS` on all three tests.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: `PASS` on everything — this is a new module; `cli.py`'s `author_from_recorded_actions` isn't touched until Task 5.

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/authoring_service.py engine/tests/test_authoring_service.py
git commit -m "feat: add authoring_service.author_from_recording, decoupled from capture"
```

---

### Task 5: Refactor `cli.py` onto the two services (backward-compatible)

**Files:**
- Modify: `engine/src/atlas_engine/cli.py`

**Interfaces:**
- `author_from_recorded_actions(org_id, start_url, actions, *, llm=None)` keeps its exact name, signature, and return shape — its body becomes `persist_recording(...)` then `author_from_recording(...)`.
- The `record` CLI command's body becomes a call to `persist_recording`, gaining `--request-id` and `--role` options (both optional, defaulting to today's behavior).

- [ ] **Step 1: Replace `author_from_recorded_actions`'s body in `engine/src/atlas_engine/cli.py`**

Replace the entire function (from `def author_from_recorded_actions(` through its `return` statement) with:

```python
def author_from_recorded_actions(
    org_id: str, start_url: str, actions: list[RecordedAction], *, llm: LlmClient | None = None
) -> dict:
    """Backward-compatible convenience wrapper: persist the recording, then
    author from it. Kept for existing callers (the author-from-actions CLI
    command and the /author API endpoint); new code should call
    recording_service.persist_recording and authoring_service.
    author_from_recording separately when the two need to happen at
    different times or by different people.
    """
    recording_id = persist_recording(org_id, start_url, actions)
    return author_from_recording(recording_id, llm=llm)
```

- [ ] **Step 2: Replace the `record` command's body**

Replace the entire `record()` function with:

```python
@main.command("record")
@click.argument("url")
@click.option("--org-id", required=True)
@click.option("--request-id", default=None)
@click.option("--role", default="demo", type=click.Choice(["demo", "authoring"]))
def record(url: str, org_id: str, request_id: str | None, role: str) -> None:
    """Open a headed browser at URL, capture actions until you press Enter,
    persist the recording, and print its ID."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(url)

        with CaptureSession(page) as capture:
            click.echo("Recording... perform your steps in the opened browser, then press Enter here to finish.")
            input()

        browser.close()

    recording_id = persist_recording(
        org_id, url, capture.actions,
        request_id=uuid.UUID(request_id) if request_id else None,
        role=role,
    )
    click.echo(f"Recording saved: {recording_id}")
```

- [ ] **Step 3: Update imports at the top of `cli.py`**

Remove now-unused imports (`extract_trajectory`, `trajectory_to_dict`, `GoldenScript`, `PortalProfile`, `Recording`, `ReferenceTrajectory`, `get_or_create_portal_profile`, `next_golden_script_version`, `infer_portal_profile`, `emit_golden_script`, `certify_golden_script` — check each is genuinely unused elsewhere in the file, e.g. `certify_golden_script`/`emit_golden_script` may still be referenced if any other function in `cli.py` uses them directly; only remove what's truly unreferenced after Steps 1-2). Add:

```python
from atlas_engine.authoring_service import author_from_recording
from atlas_engine.recording_service import persist_recording
```

- [ ] **Step 4: Run the full suite**

Run: `pytest -v`
Expected: `PASS` on everything — every Phase 0/1 test that calls `author_from_recorded_actions` or exercises the `author-from-actions` CLI command / `/author` API endpoint must still pass unmodified, since the wrapper's behavior and return shape are identical to before.

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/cli.py
git commit -m "refactor: cli.py delegates to recording_service and authoring_service"
```

---

### Task 6: New CLI commands — `request-recording`, `list-requests`, `claim-request`, `author`

**Files:**
- Modify: `engine/src/atlas_engine/cli.py`
- Create: `engine/tests/test_cli_requests.py`

**Interfaces:**
- `atlas request-recording --org-id --requested-by --target-url --title [--description]` → prints the new request id.
- `atlas list-requests --org-id [--status]` → prints one line per request.
- `atlas claim-request <request-id> --developer-id <user-id>` → confirms the claim.
- `atlas author <recording-id> [--llm-configured]` → the new, decoupled authoring command (distinct from `author-from-actions`, which still exists for the old file-based flow); prints CERTIFIED/NOT certified same as `author-from-actions` does.

- [ ] **Step 1: Write the failing test**

`engine/tests/test_cli_requests.py`:
```python
import uuid

from click.testing import CliRunner

from atlas_engine.cli import main
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, RecordingRequest, User


def _make_org_and_users(slug_prefix: str) -> tuple[str, str, str]:
    with session_scope() as session:
        org = Organization(name="CLI Requests Org", slug=f"{slug_prefix}-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        agent = User(org_id=org.id, email=f"agent-{uuid.uuid4()}@example.com", password_hash="x", role="member")
        developer = User(org_id=org.id, email=f"dev-{uuid.uuid4()}@example.com", password_hash="x", role="member")
        session.add_all([agent, developer])
        session.flush()
        return str(org.id), str(agent.id), str(developer.id)


def test_request_recording_list_and_claim_commands(fixture_portal_url):
    org_id, agent_id, developer_id = _make_org_and_users("cli-requests")
    runner = CliRunner()

    create_result = runner.invoke(
        main,
        [
            "request-recording",
            "--org-id", org_id,
            "--requested-by", agent_id,
            "--target-url", f"{fixture_portal_url}/login.html",
            "--title", "Check eligibility",
        ],
    )
    assert create_result.exit_code == 0, create_result.output
    request_id = create_result.output.strip().splitlines()[-1].split(": ")[-1]

    list_result = runner.invoke(main, ["list-requests", "--org-id", org_id, "--status", "open"])
    assert list_result.exit_code == 0, list_result.output
    assert "Check eligibility" in list_result.output

    claim_result = runner.invoke(main, ["claim-request", request_id, "--developer-id", developer_id])
    assert claim_result.exit_code == 0, claim_result.output

    with session_scope() as session:
        request = session.get(RecordingRequest, uuid.UUID(request_id))
        assert request.claimed_by == uuid.UUID(developer_id)


def test_author_command_authors_from_an_existing_recording(fixture_portal_url):
    from playwright.sync_api import sync_playwright

    from atlas_engine.capture.recorder import CaptureSession
    from atlas_engine.recording_service import persist_recording

    org_id, _agent_id, _developer_id = _make_org_and_users("cli-author")
    start_url = f"{fixture_portal_url}/login.html"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(start_url)
        with CaptureSession(page) as capture:
            page.fill("#username", "demo")
            page.fill("#password", "demo-pass")
            page.click("#login-btn")
            page.wait_for_url("**/dashboard.html")
            page.click("#generate-report-btn")
        browser.close()

    recording_id = persist_recording(org_id, start_url, capture.actions)

    runner = CliRunner()
    result = runner.invoke(main, ["author", str(recording_id)])

    assert result.exit_code == 0, result.output
    assert "CERTIFIED" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_requests.py -v`
Expected: `FAIL` — `Error: No such command 'request-recording'.`

- [ ] **Step 3: Add the four commands to `engine/src/atlas_engine/cli.py`**

Add these imports:
```python
from atlas_engine.recording_service import claim_recording_request, create_recording_request, list_recording_requests
```

Append (before `if __name__ == "__main__":`):

```python
@main.command("request-recording")
@click.option("--org-id", required=True)
@click.option("--requested-by", required=True)
@click.option("--target-url", required=True)
@click.option("--title", required=True)
@click.option("--description", default=None)
def request_recording(org_id: str, requested_by: str, target_url: str, title: str, description: str | None) -> None:
    """Operations agent: submit a request describing what needs
    automating. Follow up with `atlas record` against the same
    --request-id to attach the demo recording."""
    request_id = create_recording_request(org_id, requested_by, target_url, title, description)
    click.echo(f"Recording request created: {request_id}")


@main.command("list-requests")
@click.option("--org-id", required=True)
@click.option("--status", default=None, type=click.Choice(["open", "submitted", "fulfilled", "cancelled"]))
def list_requests(org_id: str, status: str | None) -> None:
    """Developer: list recording requests, optionally filtered by status."""
    requests = list_recording_requests(org_id, status=status)
    if not requests:
        click.echo("No recording requests found.")
        return
    for request in requests:
        claimed = f" (claimed by {request['claimed_by']})" if request["claimed_by"] else ""
        click.echo(f"{request['id']}  [{request['status']}]  {request['title']} — {request['target_url']}{claimed}")


@main.command("claim-request")
@click.argument("request_id")
@click.option("--developer-id", required=True)
def claim_request(request_id: str, developer_id: str) -> None:
    """Developer: mark a request as being worked on (advisory only)."""
    claim_recording_request(uuid.UUID(request_id), uuid.UUID(developer_id))
    click.echo(f"Claimed request {request_id}")


@main.command("author")
@click.argument("recording_id")
def author(recording_id: str) -> None:
    """Author a golden script from an already-persisted recording —
    the decoupled counterpart to author-from-actions, for recordings that
    already exist as Recording rows (via `atlas record`, by anyone)."""
    llm = create_llm_client()
    result = author_from_recording(uuid.UUID(recording_id), llm=llm)
    if result["status"] == "certified":
        click.echo(f"CERTIFIED golden script: {result['script_path']}")
    else:
        click.echo(f"NOT certified: {result['script_path']}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_requests.py -v`
Expected: `PASS` on both tests.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: `PASS` on everything.

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/cli.py engine/tests/test_cli_requests.py
git commit -m "feat: add request-recording, list-requests, claim-request, and author CLI commands"
```

---

### Task 7: API endpoints for recording requests and decoupled authoring

**Files:**
- Modify: `engine/src/atlas_engine/api.py`
- Create: `engine/tests/test_api_requests.py`

**Interfaces:**
- `POST /recording-requests` (body: `org_id`, `requested_by`, `target_url`, `title`, `description?`) → `{request_id}`.
- `GET /recording-requests?org_id=&status=` → list of request dicts.
- `POST /recording-requests/{id}/claim` (body: `developer_id`) → `{ok: true}`.
- `POST /recordings/{id}/author` → same response shape as `/author`.

- [ ] **Step 1: Write the failing test**

`engine/tests/test_api_requests.py`:
```python
import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, User


def _make_org_and_users(slug_prefix: str) -> tuple[str, str, str]:
    with session_scope() as session:
        org = Organization(name="API Requests Org", slug=f"{slug_prefix}-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        agent = User(org_id=org.id, email=f"agent-{uuid.uuid4()}@example.com", password_hash="x", role="member")
        developer = User(org_id=org.id, email=f"dev-{uuid.uuid4()}@example.com", password_hash="x", role="member")
        session.add_all([agent, developer])
        session.flush()
        return str(org.id), str(agent.id), str(developer.id)


def test_create_list_and_claim_recording_request_endpoints(fixture_portal_url):
    org_id, agent_id, developer_id = _make_org_and_users("api-requests")
    client = TestClient(app)

    create_response = client.post(
        "/recording-requests",
        json={
            "org_id": org_id, "requested_by": agent_id,
            "target_url": f"{fixture_portal_url}/login.html", "title": "Check eligibility",
        },
    )
    assert create_response.status_code == 200
    request_id = create_response.json()["request_id"]

    list_response = client.get("/recording-requests", params={"org_id": org_id, "status": "open"})
    assert list_response.status_code == 200
    assert any(r["id"] == request_id for r in list_response.json())

    claim_response = client.post(f"/recording-requests/{request_id}/claim", json={"developer_id": developer_id})
    assert claim_response.status_code == 200


def test_recording_author_endpoint(fixture_portal_url):
    from playwright.sync_api import sync_playwright

    from atlas_engine.capture.recorder import CaptureSession
    from atlas_engine.recording_service import persist_recording

    org_id, _agent_id, _developer_id = _make_org_and_users("api-author")
    start_url = f"{fixture_portal_url}/login.html"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(start_url)
        with CaptureSession(page) as capture:
            page.fill("#username", "demo")
            page.fill("#password", "demo-pass")
            page.click("#login-btn")
            page.wait_for_url("**/dashboard.html")
            page.click("#generate-report-btn")
        browser.close()

    recording_id = persist_recording(org_id, start_url, capture.actions)

    client = TestClient(app)
    response = client.post(f"/recordings/{recording_id}/author")

    assert response.status_code == 200
    assert response.json()["status"] == "certified"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_requests.py -v`
Expected: `FAIL` with `404 Not Found` on all endpoints.

- [ ] **Step 3: Add the endpoints to `engine/src/atlas_engine/api.py`**

Add these imports:
```python
from atlas_engine.authoring_service import author_from_recording
from atlas_engine.recording_service import claim_recording_request, create_recording_request, list_recording_requests
```

Append:
```python
class CreateRecordingRequestBody(BaseModel):
    org_id: str
    requested_by: str
    target_url: str
    title: str
    description: str | None = None


class RecordingRequestCreatedResponse(BaseModel):
    request_id: str


class ClaimRequestBody(BaseModel):
    developer_id: str


@app.post("/recording-requests", response_model=RecordingRequestCreatedResponse)
def create_recording_request_endpoint(body: CreateRecordingRequestBody) -> RecordingRequestCreatedResponse:
    request_id = create_recording_request(
        body.org_id, body.requested_by, body.target_url, body.title, body.description
    )
    return RecordingRequestCreatedResponse(request_id=str(request_id))


@app.get("/recording-requests")
def list_recording_requests_endpoint(org_id: str, status: str | None = None) -> list[dict]:
    return list_recording_requests(org_id, status=status)


@app.post("/recording-requests/{request_id}/claim")
def claim_recording_request_endpoint(request_id: str, body: ClaimRequestBody) -> dict:
    claim_recording_request(uuid.UUID(request_id), uuid.UUID(body.developer_id))
    return {"ok": True}


@app.post("/recordings/{recording_id}/author", response_model=AuthorResponse)
def author_from_recording_endpoint(recording_id: str) -> AuthorResponse:
    llm = create_llm_client()
    result = author_from_recording(uuid.UUID(recording_id), llm=llm)
    return AuthorResponse(**result)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_api_requests.py -v`
Expected: `PASS` on both tests.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: `PASS` on everything.

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/api.py engine/tests/test_api_requests.py
git commit -m "feat: add recording-request and recording-author API endpoints"
```

---

### Task 8: Capstone end-to-end test — ops agent to fulfilled request

**Files:**
- Create: `engine/tests/test_end_to_end_requests.py`

**Interfaces:**
- Consumes: everything from Tasks 1–7. No new production code.

- [ ] **Step 1: Write the capstone test**

`engine/tests/test_end_to_end_requests.py`:
```python
import uuid

from playwright.sync_api import sync_playwright

from atlas_engine.authoring_service import author_from_recording
from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, Organization, Recording, RecordingRequest, User
from atlas_engine.recording_service import (
    claim_recording_request,
    create_recording_request,
    list_recording_requests,
    persist_recording,
)


def _record_flow(start_url: str) -> list:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(start_url)
        with CaptureSession(page) as capture:
            page.fill("#username", "demo")
            page.fill("#password", "demo-pass")
            page.click("#login-btn")
            page.wait_for_url("**/dashboard.html")
            page.click("#generate-report-btn")
        browser.close()
    return capture.actions


def test_operations_agent_to_fulfilled_request(fixture_portal_url):
    """The full loop this plan exists for: an operations agent submits a
    request and records a demo, a developer sees it in their queue,
    claims it, records their own authoring walkthrough, and generates a
    certified golden script — which marks the request fulfilled.
    """
    with session_scope() as session:
        org = Organization(name="E2E Requests Org", slug=f"e2e-requests-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        agent = User(
            org_id=org.id, email=f"agent-{uuid.uuid4()}@example.com", password_hash="x",
            role="member", job_function="operations_agent",
        )
        developer = User(
            org_id=org.id, email=f"dev-{uuid.uuid4()}@example.com", password_hash="x",
            role="member", job_function="developer",
        )
        session.add_all([agent, developer])
        session.flush()
        org_id = str(org.id)
        agent_id = str(agent.id)
        developer_id = developer.id

    start_url = f"{fixture_portal_url}/login.html"

    # 1. Operations agent submits a request.
    request_id = create_recording_request(org_id, agent_id, start_url, "Generate the dashboard report")

    # 2. Operations agent records the demo.
    demo_actions = _record_flow(start_url)
    demo_recording_id = persist_recording(org_id, start_url, demo_actions, request_id=request_id, role="demo")

    with session_scope() as session:
        assert session.get(RecordingRequest, request_id).status == "submitted"
        assert session.get(Recording, demo_recording_id).role == "demo"

    # 3. Developer sees it in their queue and claims it.
    submitted = list_recording_requests(org_id, status="submitted")
    assert any(r["id"] == str(request_id) for r in submitted)

    claim_recording_request(request_id, developer_id)

    # 4. Developer records their own authoring walkthrough under the same request.
    dev_actions = _record_flow(start_url)
    dev_recording_id = persist_recording(org_id, start_url, dev_actions, request_id=request_id, role="authoring")

    # 5. Developer authors the golden script from their own recording.
    result = author_from_recording(dev_recording_id)
    assert result["status"] == "certified"

    with session_scope() as session:
        request = session.get(RecordingRequest, request_id)
        assert request.status == "fulfilled"
        assert request.fulfilled_golden_script_id == uuid.UUID(result["golden_script_id"])

        golden = session.get(GoldenScript, uuid.UUID(result["golden_script_id"]))
        assert golden.created_from_recording_id == dev_recording_id

    fulfilled = list_recording_requests(org_id, status="fulfilled")
    assert any(r["id"] == str(request_id) for r in fulfilled)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_end_to_end_requests.py -v`
Expected: `PASS`. Every piece already exists from Tasks 1–7; if this fails, the failure is real — fix the underlying module, do not adjust the test to match broken behavior.

- [ ] **Step 3: Run the entire suite one final time**

Run: `pytest -v`
Expected: `PASS` on every test file from Phase 0, Phase 1, and this plan.

- [ ] **Step 4: Commit**

```bash
git add engine/tests/test_end_to_end_requests.py
git commit -m "test: add operations-agent-to-fulfilled-request capstone test"
```

---

### Task 9: README updates

**Files:**
- Modify: `engine/README.md`

- [ ] **Step 1: Add a "Recording requests: two roles, one pipeline" section**

Insert after the "Healing a golden script" section:

```markdown
## Recording requests: two roles, one pipeline

Recording and authoring are separate services (`recording_service.py` /
`authoring_service.py`) so an operations agent can demonstrate a flow
without triggering codegen, and a developer can author a golden script
from *any* persisted recording — including, when you're ready to skip the
manual re-record step, the operations agent's own.

Operations agent:
```bash
atlas request-recording --org-id <id> --requested-by <agent-id> \
  --target-url "https://example.com" --title "Check eligibility"
atlas record "https://example.com" --org-id <id> --request-id <request-id> --role demo
```

Developer:
```bash
atlas list-requests --org-id <id> --status submitted
atlas claim-request <request-id> --developer-id <dev-id>
atlas record "https://example.com" --org-id <id> --request-id <request-id> --role authoring
atlas author <your-recording-id>
```

A request only becomes `fulfilled` once a `certified` script references a
recording made under it — a `draft` result leaves it `submitted` so
someone tries again.
```

- [ ] **Step 2: Commit**

```bash
git add engine/README.md
git commit -m "docs: document the recording-request workflow"
```

---

## Self-Review Notes

**Spec coverage:** the `recording_requests` table and `role`/`job_function` columns (§3) → Task 1; `persist_recording` as the single canonical recording-persistence path, reconciling and fixing the two divergent implementations Phase 0/1 had accumulated (§4) → Task 2; the request lifecycle open→submitted→fulfilled (§5) → Tasks 2–4; the module split into `recording_service`/`authoring_service` (§4) → Tasks 2–4; backward compatibility for every existing command/endpoint (§4, Global Constraints) → Task 5; the new CLI/API surface (§6) → Tasks 6–7; proof that authoring already works directly on a `demo`-role recording, satisfying the stated future direction with no further code changes → Task 4's second test and implicitly available in Task 8. Deferred on purpose, unchanged from the design doc: voice narration capture, video recording, and any real access-control enforcement of `job_function`.

**Placeholder scan:** no TBD/TODO markers; every step has literal code. The one contingency note (Task 1's migration, "fix anything that doesn't match... following the same approach used for the initial schema migration") points at a concrete, already-proven fix pattern from Phase 0, not an open-ended instruction.

**Type consistency check:** `persist_recording(org_id, start_url, actions, *, request_id=None, role="demo") -> uuid.UUID` signature is defined once in Task 2 and consumed identically by Task 5's `record` command and wrapper, Task 6's CLI test, Task 7's API test, and Task 8's capstone. `author_from_recording(recording_id, *, llm=None) -> dict` (Task 4) keeps the exact `{"status", "script_path", "golden_script_id"}` shape `author_from_recorded_actions` already used, verified explicitly by Task 5's backward-compatibility requirement. `list_recording_requests`'s return shape (`id`, `title`, `target_url`, `status`, `claimed_by`) is defined once in Task 3 and consumed identically by Task 6's CLI formatting and Task 7's API endpoint (returned as-is).
