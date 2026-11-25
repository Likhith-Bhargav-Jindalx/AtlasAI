# Action subscenarios Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an action (a `form_options` row, category `action`) have its own list of subscenarios, shown as a "Select Subscenario" dropdown directly below "Select Action" in the "Start new recording" modal — only when that action has any — and let owner/admin (or anyone with `can_manage_form_options`) manage those subscenarios from the Form options admin page.

**Architecture:** One new table, `subscenarios`, FK'd to the specific `form_options.id` it belongs to (no `org_id`/`lob` columns of its own — inherited through the parent action). CRUD endpoints and service functions mirror the existing `form_options` ones exactly, same `require_form_options_access()` guard. `recording_requests` gets one new plain-text `subscenario` column, following the exact precedent of its existing `action`/`ehr` columns (stores the chosen label, not a FK). The recording form resolves the selected action's id, queries its subscenarios, and only renders the field when there are any.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (engine), React + TanStack Query + Vitest/Testing Library (frontend). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-25-action-subscenarios-design.md`

## Global Constraints

- Mutating endpoints (`POST`, `PATCH`, and the `/all` list) require `require_form_options_access()` (`engine/src/atlas_engine/auth/deps.py:49`) — `role in ("owner", "admin")` or `can_manage_form_options`. Same dependency already used by `form_options`' own mutating endpoints — do not invent a new one.
- `subscenarios` rows have no `org_id`/`lob` of their own — scope is always derived from `action_id → form_options.org_id/lob`. Never add those columns to `Subscenario`.
- A `POST /subscenarios` whose `action_id` resolves to a `form_options` row with `category != "action"` (or doesn't exist) is a 400, not a 500 or a silent accept.
- No hard delete anywhere in this feature. "Deleting" a subscenario means `PATCH {"active": false}`, matching `form_options`.
- `recording_requests.subscenario` is a plain nullable string column, not a foreign key — matches `action`/`ehr` on the same model (`engine/src/atlas_engine/db/models/requests.py:46,57`), not `system_id` (which is a FK).
- No seed migration for `subscenarios` — the table starts empty.

---

### Task 1: `Subscenario` model + migration

**Files:**
- Modify: `engine/src/atlas_engine/db/models/masterdata.py`
- Modify: `engine/src/atlas_engine/db/models/__init__.py`
- Create: a new Alembic migration under `engine/migrations/versions/`
- Test: `engine/tests/test_recording_service.py` (append after the last test function)

**Interfaces:**
- Produces: `atlas_engine.db.models.Subscenario` (columns: `id: uuid.UUID`, `action_id: uuid.UUID`, `label: str`, `active: bool`, `created_at: datetime`), importable as `from atlas_engine.db.models import Subscenario`. Unique constraint on `(action_id, label)`.

- [ ] **Step 1: Add the model**

Append to `engine/src/atlas_engine/db/models/masterdata.py` (end of file, after `FormOption`):

```python


# A named sub-scenario under one specific action (e.g. "Denial Correction" →
# "Bundled Denial" / "Timely Filing Denial") — see
# docs/superpowers/specs/2026-11-25-action-subscenarios-design.md. No
# org_id/lob columns: scope is always inherited through action_id, so there
# is nothing here that could drift out of sync with its parent FormOption.
class Subscenario(Base):
    __tablename__ = "subscenarios"
    __table_args__ = (UniqueConstraint("action_id", "label"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("form_options.id", ondelete="CASCADE"))
    label: Mapped[str]
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    action: Mapped["FormOption"] = relationship()
```

- [ ] **Step 2: Export it**

In `engine/src/atlas_engine/db/models/__init__.py`:

Change:
```python
from atlas_engine.db.models.masterdata import CarcCode, Client, FormOption, Payer, System
```
to:
```python
from atlas_engine.db.models.masterdata import CarcCode, Client, FormOption, Payer, Subscenario, System
```

Add `"Subscenario",` to the `__all__` list, alphabetically between `"System",` ... actually between `"SecretsRef",` and `"System",` (keep the list's existing alphabetical order).

- [ ] **Step 3: Generate the migration**

```bash
cd engine && source .venv/bin/activate
alembic revision -m "add subscenarios table"
```

This creates a new file under `migrations/versions/` with `revision`/`down_revision`/`create_date` already filled in (down_revision will be today's head). Leave those header lines exactly as generated. Replace the rest of the file's contents with:

```python
from alembic import op
import sqlalchemy as sa

# ... keep the auto-generated revision/down_revision/branch_labels/depends_on lines above this ...


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "subscenarios",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("action_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["action_id"], ["form_options.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_id", "label"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("subscenarios")
```

- [ ] **Step 4: Run the migration**

```bash
cd engine && source .venv/bin/activate
alembic upgrade head
```

Expected: no errors.

- [ ] **Step 5: Add a DB-level unit test for the table itself**

Append to `engine/tests/test_recording_service.py`:

```python
def test_subscenario_model_enforces_uniqueness_per_action_and_label(fixture_portal_url):
    from sqlalchemy.exc import IntegrityError
    from atlas_engine.db.models import FormOption, Subscenario

    with session_scope() as session:
        org = Organization(name="Subscenario Model Org", slug=f"subscenario-model-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        action = FormOption(org_id=org.id, lob="AR", category="action", label="Denial Correction")
        session.add(action)
        session.flush()
        action_id = action.id
        session.add(Subscenario(action_id=action_id, label="Bundled Denial"))
        session.flush()

    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(Subscenario(action_id=action_id, label="Bundled Denial"))
            session.flush()
```

- [ ] **Step 6: Run the test**

```bash
cd engine && source .venv/bin/activate
pytest tests/test_recording_service.py -k subscenario_model -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add engine/src/atlas_engine/db/models/masterdata.py engine/src/atlas_engine/db/models/__init__.py engine/migrations/versions/ engine/tests/test_recording_service.py
git commit -m "feat: add subscenarios table, FK'd to form_options actions"
```

---

### Task 2: Service functions for subscenario CRUD

**Files:**
- Modify: `engine/src/atlas_engine/recording_service.py` (append after `update_form_option`, currently ending at line 790)
- Test: `engine/tests/test_recording_service.py`

**Interfaces:**
- Consumes: `Subscenario`, `FormOption` (Task 1), `session_scope` (already imported in this file).
- Produces:
  - `list_subscenarios(action_id: str, *, include_inactive: bool = False) -> list[dict]` — each dict: `{"id": str, "label": str, "active": bool}`, ordered by `label`.
  - `create_subscenario(action_id: str, label: str) -> dict` — returns `{"id": str, "label": str, "active": bool}`. Raises `ValueError` if `action_id` doesn't resolve to a `FormOption` with `category == "action"`. Raises `sqlalchemy.exc.IntegrityError` on a duplicate `(action_id, label)` (caller catches it).
  - `update_subscenario(subscenario_id: str, *, label: str | None = None, active: bool | None = None) -> dict` — "not None means set" convention, matching `update_form_option`. Raises `ValueError` if the id doesn't exist.

- [ ] **Step 1: Write the failing tests**

Append to `engine/tests/test_recording_service.py`:

```python
def test_list_subscenarios_filters_by_action_and_active(fixture_portal_url):
    from atlas_engine.db.models import FormOption, Subscenario
    from atlas_engine.recording_service import list_subscenarios

    with session_scope() as session:
        org = Organization(name="List Subscenarios Org", slug=f"list-subscenarios-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        action_a = FormOption(org_id=org.id, lob="AR", category="action", label="Denial Correction")
        action_b = FormOption(org_id=org.id, lob="AR", category="action", label="Claim Submission")
        session.add_all([action_a, action_b])
        session.flush()
        action_a_id, action_b_id = str(action_a.id), str(action_b.id)
        session.add_all([
            Subscenario(action_id=action_a.id, label="Bundled Denial", active=True),
            Subscenario(action_id=action_a.id, label="Timely Filing Denial", active=False),
            Subscenario(action_id=action_b.id, label="Unrelated", active=True),
        ])

    assert [s["label"] for s in list_subscenarios(action_a_id)] == ["Bundled Denial"]
    all_for_a = list_subscenarios(action_a_id, include_inactive=True)
    assert sorted(s["label"] for s in all_for_a) == ["Bundled Denial", "Timely Filing Denial"]
    assert [s["label"] for s in list_subscenarios(action_b_id)] == ["Unrelated"]


def test_create_subscenario_rejects_a_non_action_form_option(fixture_portal_url):
    from atlas_engine.db.models import FormOption
    from atlas_engine.recording_service import create_subscenario

    with session_scope() as session:
        org = Organization(name="Create Subscenario Org", slug=f"create-subscenario-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        client_option = FormOption(org_id=org.id, lob="AR", category="client", label="Acme Clinic")
        session.add(client_option)
        session.flush()
        client_option_id = str(client_option.id)

    with pytest.raises(ValueError):
        create_subscenario(client_option_id, "Should Not Work")

    with pytest.raises(ValueError):
        create_subscenario(str(uuid.uuid4()), "No Such Action")


def test_create_subscenario_raises_integrity_error_on_duplicate(fixture_portal_url):
    from sqlalchemy.exc import IntegrityError
    from atlas_engine.db.models import FormOption
    from atlas_engine.recording_service import create_subscenario

    with session_scope() as session:
        org = Organization(name="Dup Subscenario Org", slug=f"dup-subscenario-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        action = FormOption(org_id=org.id, lob="AR", category="action", label="Denial Correction")
        session.add(action)
        session.flush()
        action_id = str(action.id)

    created = create_subscenario(action_id, "Bundled Denial")
    assert created["label"] == "Bundled Denial"
    assert created["active"] is True

    with pytest.raises(IntegrityError):
        create_subscenario(action_id, "Bundled Denial")


def test_update_subscenario_sets_only_provided_fields(fixture_portal_url):
    from atlas_engine.db.models import FormOption
    from atlas_engine.recording_service import create_subscenario, list_subscenarios, update_subscenario

    with session_scope() as session:
        org = Organization(name="Update Subscenario Org", slug=f"update-subscenario-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        action = FormOption(org_id=org.id, lob="AR", category="action", label="Denial Correction")
        session.add(action)
        session.flush()
        action_id = str(action.id)

    created = create_subscenario(action_id, "Original Label")

    updated = update_subscenario(created["id"], active=False)
    assert updated["label"] == "Original Label"
    assert updated["active"] is False
    assert [s["label"] for s in list_subscenarios(action_id)] == []

    renamed = update_subscenario(created["id"], label="Renamed Subscenario")
    assert renamed["label"] == "Renamed Subscenario"
    assert renamed["active"] is False  # untouched by this call
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd engine && source .venv/bin/activate
pytest tests/test_recording_service.py -k subscenario -v
```

Expected: FAIL with `ImportError` (the four new test functions from this task fail; `test_subscenario_model_enforces_uniqueness_per_action_and_label` from Task 1 still passes).

- [ ] **Step 3: Implement the service functions**

Append to `engine/src/atlas_engine/recording_service.py` (after `update_form_option`, currently ending at line 790):

```python


def list_subscenarios(action_id: str, *, include_inactive: bool = False) -> list[dict]:
    from atlas_engine.db.models import Subscenario

    with session_scope() as session:
        query = session.query(Subscenario).filter_by(action_id=uuid.UUID(action_id))
        if not include_inactive:
            query = query.filter_by(active=True)
        rows = query.order_by(Subscenario.label).all()
        return [{"id": str(r.id), "label": r.label, "active": r.active} for r in rows]


def create_subscenario(action_id: str, label: str) -> dict:
    from atlas_engine.db.models import FormOption, Subscenario

    with session_scope() as session:
        action = session.get(FormOption, uuid.UUID(action_id))
        if action is None or action.category != "action":
            raise ValueError(f"Not an action form option: {action_id}")
        subscenario = Subscenario(action_id=uuid.UUID(action_id), label=label)
        session.add(subscenario)
        session.flush()
        return {"id": str(subscenario.id), "label": subscenario.label, "active": subscenario.active}


def update_subscenario(subscenario_id: str, *, label: str | None = None, active: bool | None = None) -> dict:
    from atlas_engine.db.models import Subscenario

    with session_scope() as session:
        subscenario = session.get(Subscenario, uuid.UUID(subscenario_id))
        if subscenario is None:
            raise ValueError(f"No such subscenario: {subscenario_id}")
        if label is not None:
            subscenario.label = label
        if active is not None:
            subscenario.active = active
        session.flush()
        return {"id": str(subscenario.id), "label": subscenario.label, "active": subscenario.active}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd engine && source .venv/bin/activate
pytest tests/test_recording_service.py -k subscenario -v
```

Expected: PASS (5 tests: the Task 1 model test plus these 4).

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/recording_service.py engine/tests/test_recording_service.py
git commit -m "feat: add list/create/update service functions for subscenarios"
```

---

### Task 3: API endpoints for subscenario CRUD

**Files:**
- Modify: `engine/src/atlas_engine/api.py`
- Test: Create `engine/tests/test_api_subscenarios.py`

**Interfaces:**
- Consumes: `list_subscenarios`, `create_subscenario`, `update_subscenario` (Task 2); `CurrentUser`, `get_current_user`, `require_form_options_access` (already imported in `api.py`).
- Produces: `GET /subscenarios`, `GET /subscenarios/all`, `POST /subscenarios`, `PATCH /subscenarios/{subscenario_id}`.

- [ ] **Step 1: Add the import**

In `engine/src/atlas_engine/api.py`, the `from atlas_engine.recording_service import (...)` block (lines 57-76) already imports `create_form_option`, `list_form_options`, `update_form_option` alphabetically. Add `create_subscenario`, `list_subscenarios`, `update_subscenario` into the same block, keeping alphabetical order:

```python
from atlas_engine.recording_service import (
    claim_recording_request,
    create_form_option,
    create_recording_folder,
    create_recording_request,
    create_subscenario,
    get_recording_detail,
    list_carc_codes,
    list_clients,
    list_form_options,
    list_page_extractions,
    list_payers,
    list_recording_folders,
    list_recording_requests,
    list_recordings,
    list_subscenarios,
    list_systems,
    persist_recording,
    submit_recording_for_authoring,
    update_form_option,
    update_recording_details,
    update_subscenario,
)
```

- [ ] **Step 2: Write the failing tests**

Create `engine/tests/test_api_subscenarios.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd engine && source .venv/bin/activate
pytest tests/test_api_subscenarios.py -v
```

Expected: FAIL (404s — routes don't exist yet).

- [ ] **Step 4: Implement the endpoints**

Add to `engine/src/atlas_engine/api.py`, immediately after `update_form_option_endpoint` (the `/form-options/{form_option_id}` PATCH handler):

```python
class CreateSubscenarioBody(BaseModel):
    action_id: str
    label: str


class UpdateSubscenarioBody(BaseModel):
    label: str | None = None
    active: bool | None = None


@app.get("/subscenarios")
def list_subscenarios_endpoint(
    action_id: str, _user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    return list_subscenarios(action_id)


@app.get("/subscenarios/all")
def list_all_subscenarios_endpoint(
    action_id: str, _user: CurrentUser = Depends(require_form_options_access()),
) -> list[dict]:
    return list_subscenarios(action_id, include_inactive=True)


@app.post("/subscenarios")
def create_subscenario_endpoint(
    body: CreateSubscenarioBody, _user: CurrentUser = Depends(require_form_options_access()),
) -> dict:
    try:
        return create_subscenario(body.action_id, body.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f'"{body.label}" already exists for this action')


@app.patch("/subscenarios/{subscenario_id}")
def update_subscenario_endpoint(
    subscenario_id: str, body: UpdateSubscenarioBody, _user: CurrentUser = Depends(require_form_options_access()),
) -> dict:
    return update_subscenario(subscenario_id, label=body.label, active=body.active)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd engine && source .venv/bin/activate
pytest tests/test_api_subscenarios.py -v
```

Expected: PASS.

- [ ] **Step 6: Run the full backend test suite**

```bash
cd engine && source .venv/bin/activate
pytest -v
```

Expected: PASS (no regressions).

- [ ] **Step 7: Commit**

```bash
git add engine/src/atlas_engine/api.py engine/tests/test_api_subscenarios.py
git commit -m "feat: add /subscenarios endpoints, form-options-access gated for mutations"
```

---

### Task 4: `recording_requests.subscenario` column + persistence plumbing

**Files:**
- Modify: `engine/src/atlas_engine/db/models/requests.py`
- Create: a new Alembic migration under `engine/migrations/versions/`
- Modify: `engine/src/atlas_engine/recording_service.py` (`create_recording_request`, `list_recording_requests`, `list_recordings`, `get_recording_detail`)
- Modify: `engine/src/atlas_engine/api.py` (`CreateRecordingRequestBody`, `create_recording_request_endpoint`)
- Test: `engine/tests/test_recording_service.py`

**Interfaces:**
- Produces: `RecordingRequest.subscenario: str | None`. `create_recording_request(..., subscenario: str | None = None)`. `list_recording_requests` and `list_recordings`/`get_recording_detail` results each gain a `"subscenario"` key.

- [ ] **Step 1: Add the column to the model**

In `engine/src/atlas_engine/db/models/requests.py`, change:

```python
    ehr: Mapped[str | None] = mapped_column(default=None)
    specific_to: Mapped[str | None] = mapped_column(default=None)
```

to:

```python
    ehr: Mapped[str | None] = mapped_column(default=None)
    specific_to: Mapped[str | None] = mapped_column(default=None)
    # Chosen subscenario label (see subscenarios table) — plain text, not a
    # FK, following the same precedent as action/ehr above: the request
    # stores what was picked, independent of later edits to the lookup row.
    subscenario: Mapped[str | None] = mapped_column(default=None)
```

- [ ] **Step 2: Generate and write the migration**

```bash
cd engine && source .venv/bin/activate
alembic revision -m "add subscenario column to recording_requests"
```

Replace the generated file's body (keep the auto-generated header lines) with:

```python
from alembic import op
import sqlalchemy as sa

# ... keep the auto-generated revision/down_revision/branch_labels/depends_on lines above this ...


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("recording_requests", sa.Column("subscenario", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("recording_requests", "subscenario")
```

- [ ] **Step 3: Run the migration**

```bash
cd engine && source .venv/bin/activate
alembic upgrade head
```

Expected: no errors.

- [ ] **Step 4: Write the failing test**

Append to `engine/tests/test_recording_service.py`:

```python
def test_create_recording_request_persists_subscenario(fixture_portal_url):
    from atlas_engine.recording_service import list_recording_requests

    with session_scope() as session:
        org = Organization(name="Subscenario Field Org", slug=f"subscenario-field-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        agent = User(org_id=org.id, email="subscfield@example.com", password_hash="x", job_function="operations_agent")
        session.add(agent)
        session.flush()
        agent_id = str(agent.id)

    request_id = create_recording_request(
        org_id, agent_id, f"{fixture_portal_url}/login.html", "Bundled Denial on Availity",
        lob="AR", action="Denial Correction", subscenario="Bundled Denial",
    )

    with session_scope() as session:
        request = session.get(RecordingRequest, request_id)
        assert request.subscenario == "Bundled Denial"

    listed = list_recording_requests(org_id)
    assert listed[0]["subscenario"] == "Bundled Denial"
```

- [ ] **Step 5: Run the test to verify it fails**

```bash
cd engine && source .venv/bin/activate
pytest tests/test_recording_service.py -k persists_subscenario -v
```

Expected: FAIL with `TypeError: create_recording_request() got an unexpected keyword argument 'subscenario'`.

- [ ] **Step 6: Wire `subscenario` through the service layer**

In `engine/src/atlas_engine/recording_service.py`:

In `create_recording_request` (currently lines 650-681), change the signature:

```python
def create_recording_request(
    org_id: str, requested_by: str, target_url: str, title: str, description: str | None = None,
    *, lob: str | None = None, category: str | None = None, action: str | None = None,
    system_id: str | None = None, client_scope: list[str] | None = None, reuse_scope: str | None = None,
    ehr: str | None = None, specific_to: str | None = None, subscenario: str | None = None,
) -> uuid.UUID:
```

and add `subscenario=subscenario,` to the `RecordingRequest(...)` constructor call, right after `specific_to=specific_to,`.

In `list_recording_requests` (currently lines 684-722), add `"subscenario": r.subscenario,` to the returned dict, right after `"specific_to": r.specific_to,`.

In `list_recordings` (the dict built around line 357-372), add `"subscenario": request.subscenario if request is not None else None,` right after the existing `"action": request.action if request is not None else None,` line.

In `get_recording_detail` (the dict built around line 480-497), add `"subscenario": request.subscenario if request is not None else None,` right after the existing `"action": request.action if request is not None else None,` line.

- [ ] **Step 7: Run the test to verify it passes**

```bash
cd engine && source .venv/bin/activate
pytest tests/test_recording_service.py -k persists_subscenario -v
```

Expected: PASS.

- [ ] **Step 8: Wire `subscenario` through the API**

In `engine/src/atlas_engine/api.py`, `CreateRecordingRequestBody` (currently lines 212-227), add after `specific_to: str | None = None`:

```python
    subscenario: str | None = None
```

In `create_recording_request_endpoint`, add `subscenario=body.subscenario,` to the `create_recording_request(...)` call, after `ehr=body.ehr, specific_to=body.specific_to,`.

- [ ] **Step 9: Run the full backend test suite**

```bash
cd engine && source .venv/bin/activate
pytest -v
```

Expected: PASS (no regressions).

- [ ] **Step 10: Commit**

```bash
git add engine/src/atlas_engine/db/models/requests.py engine/migrations/versions/ engine/src/atlas_engine/recording_service.py engine/src/atlas_engine/api.py engine/tests/test_recording_service.py
git commit -m "feat: persist the chosen subscenario on recording_requests"
```

---

### Task 5: Frontend data hooks for subscenarios

**Files:**
- Create: `frontend/src/lib/subscenarios.ts`
- Test: Create `frontend/src/lib/subscenarios.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` (`frontend/src/lib/api.ts`).
- Produces:
  - `interface Subscenario { id: string; label: string; active: boolean }`
  - `useSubscenarios(actionId: string | null)` → `useQuery` result of `Subscenario[]` (active only), `enabled: !!actionId`.
  - `useAllSubscenarios(actionId: string | null)` → `useQuery` result of `Subscenario[]` (including inactive), `enabled: !!actionId`.
  - `useCreateSubscenario()` → `useMutation` taking `{ action_id: string; label: string }`, invalidates `['subscenarios']`.
  - `useUpdateSubscenario()` → `useMutation` taking `{ id: string; label?: string; active?: boolean }`, invalidates `['subscenarios']`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/subscenarios.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { useAllSubscenarios, useCreateSubscenario, useSubscenarios, useUpdateSubscenario } from './subscenarios'

let queryClient: QueryClient

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

describe('useSubscenarios', () => {
  it('fetches active subscenarios for an action id', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/subscenarios?action_id=action-1') {
        return Promise.resolve({ ok: true, status: 200, json: async () => [{ id: '1', label: 'Bundled Denial', active: true }] })
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    const { result } = renderHook(() => useSubscenarios('action-1'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([{ id: '1', label: 'Bundled Denial', active: true }])
  })

  it('does not fetch when actionId is null', () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('should not fetch'))))
    const { result } = renderHook(() => useSubscenarios(null), { wrapper })
    expect(result.current.fetchStatus).toBe('idle')
  })
})

describe('useAllSubscenarios', () => {
  it('fetches from the /all endpoint', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/subscenarios/all?action_id=action-1') {
        return Promise.resolve({
          ok: true, status: 200,
          json: async () => [{ id: '1', label: 'Active', active: true }, { id: '2', label: 'Inactive', active: false }],
        })
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    const { result } = renderHook(() => useAllSubscenarios('action-1'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toHaveLength(2)
  })
})

describe('useCreateSubscenario', () => {
  it('POSTs the new subscenario', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/subscenarios' && init?.method === 'POST') {
        const body = JSON.parse(init.body as string)
        expect(body).toEqual({ action_id: 'action-1', label: 'New Subscenario' })
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ id: '3', label: 'New Subscenario', active: true }) })
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    const { result } = renderHook(() => useCreateSubscenario(), { wrapper })
    result.current.mutate({ action_id: 'action-1', label: 'New Subscenario' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
  })
})

describe('useUpdateSubscenario', () => {
  it('PATCHes the subscenario', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/subscenarios/3' && init?.method === 'PATCH') {
        const body = JSON.parse(init.body as string)
        expect(body).toEqual({ active: false })
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ id: '3', label: 'New Subscenario', active: false }) })
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    const { result } = renderHook(() => useUpdateSubscenario(), { wrapper })
    result.current.mutate({ id: '3', active: false })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend && npm test -- subscenarios.test.tsx
```

Expected: FAIL (`Failed to resolve import "./subscenarios"`).

- [ ] **Step 3: Implement the hooks**

Create `frontend/src/lib/subscenarios.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface Subscenario {
  id: string
  label: string
  active: boolean
}

function queryKey(suffix: 'active' | 'all', actionId: string | null) {
  return ['subscenarios', suffix, actionId] as const
}

export function useSubscenarios(actionId: string | null) {
  return useQuery({
    queryKey: queryKey('active', actionId),
    queryFn: () => apiFetch<Subscenario[]>(`/subscenarios?action_id=${actionId}`),
    enabled: !!actionId,
  })
}

export function useAllSubscenarios(actionId: string | null) {
  return useQuery({
    queryKey: queryKey('all', actionId),
    queryFn: () => apiFetch<Subscenario[]>(`/subscenarios/all?action_id=${actionId}`),
    enabled: !!actionId,
  })
}

export function useCreateSubscenario() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { action_id: string; label: string }) =>
      apiFetch<Subscenario>('/subscenarios', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['subscenarios'] }),
  })
}

export function useUpdateSubscenario() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; label?: string; active?: boolean }) =>
      apiFetch<Subscenario>(`/subscenarios/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['subscenarios'] }),
  })
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd frontend && npm test -- subscenarios.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/subscenarios.ts frontend/src/lib/subscenarios.test.tsx
git commit -m "feat: add React Query hooks for subscenarios"
```

---

### Task 6: Wire the recording form, InboxPage, and recordingRequests.ts

**Files:**
- Modify: `frontend/src/components/RecordingFormSectionA.tsx`
- Modify: `frontend/src/pages/agent/InboxPage.tsx`
- Modify: `frontend/src/lib/recordingRequests.ts`
- Test: `frontend/src/components/RecordingFormSectionA.test.tsx`

**Interfaces:**
- Consumes: `useSubscenarios` (Task 5).
- Produces: `SectionAValues.subscenario: string`.

- [ ] **Step 1: Write the failing test**

In `frontend/src/components/RecordingFormSectionA.test.tsx`, replace the whole file with:

```tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { RecordingFormSectionA } from './RecordingFormSectionA'

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/form-options?lob=AR&category=client') return Promise.resolve(jsonResponse([{ id: '1', label: 'Acme Clinic', active: true }]))
    if (path === '/form-options?lob=AR&category=action') {
      return Promise.resolve(jsonResponse([
        { id: '2', label: 'Claim Submission', active: true },
        { id: '4', label: 'Denial Correction', active: true },
      ]))
    }
    if (path === '/form-options?lob=AR&category=ehr') return Promise.resolve(jsonResponse([{ id: '3', label: 'eCW', active: true }]))
    if (path === '/subscenarios?action_id=2') return Promise.resolve(jsonResponse([]))
    if (path === '/subscenarios?action_id=4') return Promise.resolve(jsonResponse([{ id: '5', label: 'Bundled Denial', active: true }]))
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderForm() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <RecordingFormSectionA onSubmit={vi.fn()} onCancel={vi.fn()} isSubmitting={false} />
    </QueryClientProvider>,
  )
}

function openDropdown(label: string) {
  const dropdownLabel = screen.getByText(label)
  const toggle = dropdownLabel.parentElement!.querySelector('button')!
  fireEvent.click(toggle)
}

describe('RecordingFormSectionA', () => {
  it('loads Client options from the API for the active LOB', async () => {
    renderForm()
    openDropdown('Select Client')
    await waitFor(() => expect(screen.getByText('Acme Clinic')).toBeInTheDocument())
  })

  it('does not show a subscenario field before an action is selected', () => {
    renderForm()
    expect(screen.queryByText('Select Subscenario')).toBeNull()
  })

  it('does not show a subscenario field for an action with none', async () => {
    renderForm()
    openDropdown('Select Action')
    fireEvent.click(await screen.findByText('Claim Submission'))
    await waitFor(() => expect(screen.queryByText('Select Subscenario')).toBeNull())
  })

  it('shows and loads the subscenario field for an action that has some', async () => {
    renderForm()
    openDropdown('Select Action')
    fireEvent.click(await screen.findByText('Denial Correction'))
    await waitFor(() => expect(screen.getByText('Select Subscenario')).toBeInTheDocument())
    openDropdown('Select Subscenario')
    await waitFor(() => expect(screen.getByText('Bundled Denial')).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend && npm test -- RecordingFormSectionA.test.tsx
```

Expected: FAIL (the three new tests — "Select Subscenario" never renders yet).

- [ ] **Step 3: Wire the component**

In `frontend/src/components/RecordingFormSectionA.tsx`:

Change the import block (currently lines 1-4):

```ts
import { useEffect, useRef, useState } from 'react'
import { ChevronDown, Search, X } from 'lucide-react'
import { LOB_TABS, SPECIFICITY_OPTIONS, suggestWorkflowName, type Lob } from '../lib/recordingFormSchema'
import { useFormOptions } from '../lib/formOptions'
```

to:

```ts
import { useEffect, useRef, useState } from 'react'
import { ChevronDown, Search, X } from 'lucide-react'
import { LOB_TABS, SPECIFICITY_OPTIONS, suggestWorkflowName, type Lob } from '../lib/recordingFormSchema'
import { useFormOptions } from '../lib/formOptions'
import { useSubscenarios } from '../lib/subscenarios'
```

Add `subscenario: string` to the `SectionAValues` interface, after `action: string`:

```ts
export interface SectionAValues {
  targetUrl: string
  workflowName: string
  lob: Lob
  client: string
  action: string
  subscenario: string
  specificTo: string
  ehr: string
}
```

Inside `RecordingFormSectionA`, add a `subscenario` state next to the existing `action` state:

```ts
  const [action, setAction] = useState('')
  const [subscenario, setSubscenario] = useState('')
```

Right after the existing:

```ts
  const clientOptions = useFormOptions(lob, 'client').data ?? []
  const actionOptions = useFormOptions(lob, 'action').data ?? []
  const ehrOptions = useFormOptions(lob, 'ehr').data ?? []
```

add:

```ts
  const actionId = actionOptions.find((o) => o.label === action)?.id ?? null
  const subscenarioOptions = useSubscenarios(actionId).data ?? []
```

In `selectLob`, reset `subscenario` alongside `action`:

```ts
  function selectLob(next: Lob) {
    setLob(next)
    setClient('')
    setAction('')
    setSubscenario('')
    setEhr('')
    setWorkflowNameTouched(false)
  }
```

Add a handler that resets `subscenario` whenever `action` changes — change the `Select Action` dropdown's `onChange` from `setAction` to:

```tsx
          <SearchableSelect
            label="Select Action"
            options={actionOptions.map((o) => o.label)}
            value={action}
            onChange={(next) => { setAction(next); setSubscenario('') }}
            placeholder="Type to search…"
          />

          {subscenarioOptions.length > 0 && (
            <SearchableSelect
              label="Select Subscenario"
              options={subscenarioOptions.map((o) => o.label)}
              value={subscenario}
              onChange={setSubscenario}
              placeholder="Type to search…"
            />
          )}
```

(this replaces the existing `Select Action` `SearchableSelect` block and inserts the new conditional block directly after it, still above "Specific to").

Finally, add `subscenario` to the submitted values:

```tsx
              onSubmit({ targetUrl: targetUrl.trim(), workflowName: effectiveTitle, lob, client, action, subscenario, specificTo, ehr })
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd frontend && npm test -- RecordingFormSectionA.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Wire InboxPage and recordingRequests.ts**

In `frontend/src/lib/recordingRequests.ts`, add `subscenario: string | null` to `RecordingRequestSummary` (after `action: string | null`) and `subscenario?: string` to `CreateRecordingRequestBody` (after `action?: string`).

In `frontend/src/pages/agent/InboxPage.tsx`, in `handleCreate`, add `subscenario: values.subscenario || undefined,` right after the existing `action: values.action || undefined,` line.

- [ ] **Step 6: Run the full frontend test suite**

```bash
cd frontend && npm test
```

Expected: PASS (no regressions).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/RecordingFormSectionA.tsx frontend/src/components/RecordingFormSectionA.test.tsx frontend/src/pages/agent/InboxPage.tsx frontend/src/lib/recordingRequests.ts
git commit -m "feat: show a subscenario dropdown below Select Action when one has any"
```

---

### Task 7: Admin UI — manage subscenarios per action

**Files:**
- Modify: `frontend/src/pages/dev/FormOptionsPage.tsx`
- Test: `frontend/src/pages/dev/FormOptionsPage.test.tsx`

**Interfaces:**
- Consumes: `useAllSubscenarios`, `useCreateSubscenario`, `useUpdateSubscenario` (Task 5).

- [ ] **Step 1: Write the failing test**

In `frontend/src/pages/dev/FormOptionsPage.test.tsx`, replace the whole file with:

```tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { FormOptionsPage } from './FormOptionsPage'

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

let options = [{ id: '1', label: 'Claim Submission', active: true }]
let subscenarios: { id: string; label: string; active: boolean }[] = []

beforeEach(() => {
  options = [{ id: '1', label: 'Claim Submission', active: true }]
  subscenarios = []
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/form-options/all?lob=AR&category=action') return Promise.resolve(jsonResponse(options))
    if (path === '/form-options' && init?.method === 'POST') {
      const body = JSON.parse(init.body as string)
      options = [...options, { id: '2', label: body.label, active: true }]
      return Promise.resolve(jsonResponse({ id: '2', label: body.label, active: true }))
    }
    if (path === '/form-options/1' && init?.method === 'PATCH') {
      options = options.map((o) => (o.id === '1' ? { ...o, active: false } : o))
      return Promise.resolve(jsonResponse({ id: '1', label: 'Claim Submission', active: false }))
    }
    if (path === '/subscenarios/all?action_id=1') return Promise.resolve(jsonResponse(subscenarios))
    if (path === '/subscenarios' && init?.method === 'POST') {
      const body = JSON.parse(init.body as string)
      subscenarios = [...subscenarios, { id: 's1', label: body.label, active: true }]
      return Promise.resolve(jsonResponse({ id: 's1', label: body.label, active: true }))
    }
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <FormOptionsPage />
    </QueryClientProvider>,
  )
}

describe('FormOptionsPage', () => {
  it('lists existing options for the default lob/category', async () => {
    renderPage()
    expect(await screen.findByText('Claim Submission')).toBeInTheDocument()
  })

  it('adds a new option', async () => {
    renderPage()
    await screen.findByText('Claim Submission')
    fireEvent.change(screen.getByPlaceholderText('New option label'), { target: { value: 'New Action' } })
    fireEvent.click(screen.getByText('Add'))
    await waitFor(() => expect(screen.getByText('New Action')).toBeInTheDocument())
  })

  it('deactivates an option', async () => {
    renderPage()
    await screen.findByText('Claim Submission')
    fireEvent.click(screen.getByLabelText('Deactivate Claim Submission'))
    await waitFor(() => expect(screen.getByLabelText('Deactivate Claim Submission')).not.toBeChecked())
  })

  it('expands an action row to add a subscenario', async () => {
    renderPage()
    await screen.findByText('Claim Submission')
    fireEvent.click(screen.getByLabelText('Manage subscenarios for Claim Submission'))
    const input = await screen.findByPlaceholderText('New subscenario label')
    fireEvent.change(input, { target: { value: 'Bundled Denial' } })
    fireEvent.click(screen.getByText('Add subscenario'))
    await waitFor(() => expect(screen.getByText('Bundled Denial')).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend && npm test -- FormOptionsPage.test.tsx
```

Expected: FAIL (the new "expands an action row" test — no expand toggle exists yet).

- [ ] **Step 3: Implement the nested subscenario UI**

Replace `frontend/src/pages/dev/FormOptionsPage.tsx` in full with:

```tsx
import { Fragment, useState } from 'react'
import { LOB_TABS, type Lob } from '../../lib/recordingFormSchema'
import { useAllFormOptions, useCreateFormOption, useUpdateFormOption, type FormOptionCategory } from '../../lib/formOptions'
import { useAllSubscenarios, useCreateSubscenario, useUpdateSubscenario } from '../../lib/subscenarios'

const CATEGORY_TABS: { category: FormOptionCategory; label: string }[] = [
  { category: 'client', label: 'Client' },
  { category: 'action', label: 'Action' },
  { category: 'ehr', label: 'EHR' },
]

function SubscenarioManager({ actionId, actionLabel }: { actionId: string; actionLabel: string }) {
  const [newLabel, setNewLabel] = useState('')
  const { data: subscenarios = [] } = useAllSubscenarios(actionId)
  const createSubscenario = useCreateSubscenario()
  const updateSubscenario = useUpdateSubscenario()

  function handleAdd() {
    const label = newLabel.trim()
    if (!label) return
    createSubscenario.mutate({ action_id: actionId, label }, { onSuccess: () => setNewLabel('') })
  }

  return (
    <div className="flex flex-col gap-3 pl-4 py-3 bg-atlas-bg">
      <table className="w-full text-[12.5px] border border-atlas-border rounded-[10px] overflow-hidden">
        <thead>
          <tr className="bg-atlas-card text-left">
            <th className="px-3 py-1.5">Subscenario</th>
            <th className="px-3 py-1.5">Active</th>
          </tr>
        </thead>
        <tbody>
          {subscenarios.map((subscenario) => (
            <tr key={subscenario.id} className="border-t border-atlas-border">
              <td className="px-3 py-1.5">{subscenario.label}</td>
              <td className="px-3 py-1.5">
                <input
                  type="checkbox"
                  aria-label={`Deactivate ${subscenario.label}`}
                  checked={subscenario.active}
                  onChange={(e) => updateSubscenario.mutate({ id: subscenario.id, active: e.target.checked })}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex gap-2">
        <input
          value={newLabel}
          onChange={(e) => setNewLabel(e.target.value)}
          placeholder="New subscenario label"
          className="px-3 py-2 rounded-[10px] border border-atlas-border outline-none text-[12.5px] flex-1"
        />
        <button
          onClick={handleAdd}
          disabled={!newLabel.trim() || createSubscenario.isPending}
          className="bg-atlas-primary text-white rounded-full px-3 text-[12.5px] font-medium disabled:opacity-60"
        >
          Add subscenario
        </button>
      </div>
      <div className="text-[11px] text-atlas-muted">Subscenarios for {actionLabel}</div>
    </div>
  )
}

export function FormOptionsPage() {
  const [lob, setLob] = useState<Lob>('AR')
  const [category, setCategory] = useState<FormOptionCategory>('action')
  const [newLabel, setNewLabel] = useState('')
  const [expandedActionId, setExpandedActionId] = useState<string | null>(null)

  const { data: options = [] } = useAllFormOptions(lob, category)
  const createOption = useCreateFormOption()
  const updateOption = useUpdateFormOption()

  function handleAdd() {
    const label = newLabel.trim()
    if (!label) return
    createOption.mutate({ lob, category, label }, { onSuccess: () => setNewLabel('') })
  }

  return (
    <div className="p-8 flex flex-col gap-6 max-w-2xl">
      <div className="font-display text-lg font-semibold tracking-tight">Recording form options</div>

      <div className="flex gap-1">
        {LOB_TABS.map((tab) => (
          <button
            key={tab.lob}
            onClick={() => setLob(tab.lob)}
            className={`px-4 py-2 rounded-full text-[13px] font-medium border ${
              lob === tab.lob ? 'bg-atlas-primary text-white border-atlas-primary' : 'border-atlas-border text-atlas-body'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="flex gap-1">
        {CATEGORY_TABS.map((tab) => (
          <button
            key={tab.category}
            onClick={() => setCategory(tab.category)}
            className={`px-4 py-2 rounded-full text-[13px] font-medium border ${
              category === tab.category ? 'bg-atlas-primary text-white border-atlas-primary' : 'border-atlas-border text-atlas-body'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <table className="w-full text-sm border border-atlas-border rounded-[10px] overflow-hidden">
        <thead>
          <tr className="bg-atlas-bg text-left">
            <th className="px-3 py-2">Label</th>
            <th className="px-3 py-2">Active</th>
            {category === 'action' && <th className="px-3 py-2" />}
          </tr>
        </thead>
        <tbody>
          {options.map((option) => (
            <Fragment key={option.id}>
              <tr className="border-t border-atlas-border">
                <td className="px-3 py-2">{option.label}</td>
                <td className="px-3 py-2">
                  <input
                    type="checkbox"
                    aria-label={`Deactivate ${option.label}`}
                    checked={option.active}
                    onChange={(e) => updateOption.mutate({ id: option.id, active: e.target.checked })}
                  />
                </td>
                {category === 'action' && (
                  <td className="px-3 py-2">
                    <button
                      aria-label={`Manage subscenarios for ${option.label}`}
                      onClick={() => setExpandedActionId(expandedActionId === option.id ? null : option.id)}
                      className="text-[12px] text-atlas-primary font-medium"
                    >
                      {expandedActionId === option.id ? 'Hide' : 'Subscenarios'}
                    </button>
                  </td>
                )}
              </tr>
              {category === 'action' && expandedActionId === option.id && (
                <tr>
                  <td colSpan={3} className="p-0">
                    <SubscenarioManager actionId={option.id} actionLabel={option.label} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>

      <div className="flex gap-2">
        <input
          value={newLabel}
          onChange={(e) => setNewLabel(e.target.value)}
          placeholder="New option label"
          className="px-3.5 py-2.5 rounded-[10px] border border-atlas-border outline-none text-sm flex-1"
        />
        <button
          onClick={handleAdd}
          disabled={!newLabel.trim() || createOption.isPending}
          className="bg-atlas-primary text-white rounded-full px-4 text-[13.5px] font-medium disabled:opacity-60"
        >
          Add
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd frontend && npm test -- FormOptionsPage.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Run the full frontend test suite**

```bash
cd frontend && npm test
```

Expected: PASS (no regressions).

- [ ] **Step 6: Manually verify in the browser**

```bash
cd frontend && npm run dev
```

As an owner/admin: go to Form options, Action tab, click "Subscenarios" next to an action, add one, confirm it appears. Then go to "Start new recording", pick that action, confirm "Select Subscenario" appears and lists it; pick an action with none and confirm the field is absent.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/dev/FormOptionsPage.tsx frontend/src/pages/dev/FormOptionsPage.test.tsx
git commit -m "feat: manage subscenarios per action from the Form options admin page"
```
