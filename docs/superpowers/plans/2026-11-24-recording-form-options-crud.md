# Recording-form dropdown options CRUD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an owner/admin add, rename, and deactivate the Client/Action/EHR options shown in the "Start new recording" modal's dropdowns, from the running app, replacing today's hardcoded TypeScript arrays with a database-backed, per-org, per-LOB table.

**Architecture:** One new table (`form_options`) holding all three categories (not three tables). Read endpoint is open to any authenticated user (used by the recording form); list-all/create/update endpoints are owner/admin-only, reusing the existing `require_job_function()` dependency with no arguments. The frontend recording form switches from static imports to a React Query hook against the new read endpoint; a new admin page provides the management UI.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (engine), React + TanStack Query + Vitest/Testing Library (frontend). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-24-recording-form-options-crud-design.md`

## Global Constraints

- Mutating endpoints (`POST`, `PATCH`, and the `/all` list) require `role in ("owner", "admin")` — reuse `require_job_function()` called with no arguments (`atlas_engine/auth/deps.py:36`), matching the existing convention (e.g. `api.py:1018`).
- No hard delete. "Deleting" an option means `PATCH {"active": false}`.
- Every row is `org_id`-scoped. `org_id` always comes from `CurrentUser.org_id` (the session), never a client-supplied value — matching every other endpoint in `api.py`.
- Exactly one new table for all three categories (`client`/`action`/`ehr`), not three tables.
- `SPECIFICITY_OPTIONS`, `RESOLUTION_PATH_OPTIONS`, `EVIDENCE_REQUIRED_OPTIONS`, `PAYER_SCOPE_OPTIONS`, and the `DENIAL_LIKE_ACTIONS`/`RESOLUTION_PATH_ACTIONS`/`REPORT_ACTIONS` sets in `recordingFormSchema.ts` are out of scope — do not touch them.
- The cutover migration must seed every string currently in `CLIENT_OPTIONS`/`ACTION_OPTIONS`/`EHR_OPTIONS` (both `AR` and `EV`) so no option is lost.

---

### Task 1: `FormOption` model + migration

**Files:**
- Modify: `engine/src/atlas_engine/db/models/masterdata.py`
- Modify: `engine/src/atlas_engine/db/models/__init__.py`
- Create: a new Alembic migration under `engine/migrations/versions/`
- Test: `engine/tests/test_recording_service.py` (new test function, appended after `test_master_data_list_functions_return_only_active_rows_for_the_org` at line 920)

**Interfaces:**
- Produces: `atlas_engine.db.models.FormOption` (columns: `id: uuid.UUID`, `org_id: uuid.UUID`, `lob: str`, `category: str`, `label: str`, `active: bool`, `created_at: datetime`), importable as `from atlas_engine.db.models import FormOption`. Unique constraint on `(org_id, lob, category, label)`.

- [ ] **Step 1: Add the model**

Append to `engine/src/atlas_engine/db/models/masterdata.py` (after the `CarcCode` class, end of file):

```python
# Backs the recording form's Client/Action/EHR dropdowns (Section A) — see
# docs/superpowers/specs/2026-11-24-recording-form-options-crud-design.md.
# Unlike System/Payer/Client/CarcCode above, this one DOES have admin CRUD
# (POST/PATCH in api.py), gated to owner/admin.
FORM_OPTION_CATEGORIES = ("client", "action", "ehr")


class FormOption(Base):
    __tablename__ = "form_options"
    __table_args__ = (UniqueConstraint("org_id", "lob", "category", "label"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    lob: Mapped[str]
    category: Mapped[str]
    label: Mapped[str]
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    org: Mapped["Organization"] = relationship()
```

- [ ] **Step 2: Export it**

In `engine/src/atlas_engine/db/models/__init__.py`, change line 5 from:

```python
from atlas_engine.db.models.masterdata import CarcCode, Client, Payer, System
```

to:

```python
from atlas_engine.db.models.masterdata import CarcCode, Client, FormOption, Payer, System
```

- [ ] **Step 3: Generate the migration**

```bash
cd engine && source .venv/bin/activate
alembic revision -m "add form options table"
```

This creates a new file under `migrations/versions/` with `revision`/`down_revision`/`create_date` already filled in (down_revision will be `'aa6b254c56a8'`, today's head). Leave those four header lines exactly as generated. Replace the rest of the file's contents with:

```python
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# ... keep the auto-generated revision/down_revision/branch_labels/depends_on lines above this ...

# Seed values: every string currently hardcoded in frontend/src/lib/recordingFormSchema.ts's
# CLIENT_OPTIONS/ACTION_OPTIONS/EHR_OPTIONS (as of 2026-11-24), so the cutover to this
# DB-backed table loses nothing. Seeded per-organization, once for every existing org.
_CLIENT_OPTIONS = {
    "AR": [
        "Audentes CSSL CSSC THSC", "Audentes CPI", "Audentes CSSA", "Audentes XRS",
        "Houston Heart Health", "Maiden Lane Medical", "MRI", "Pediatric Associates at Ridge",
        "Parkside Psychiatric Hospital Clinic", "Penta", "Tricity", "Voyage Health",
        "Urgent Care", "NHS", "PRMC",
    ],
    "EV": [
        "Audentes Verification", "Voyage sales Verification", "Voyage intake Verification",
        "Housten Heart", "Pediatrics Ridge", "MRI Memorial Verification", "MLM", "TUC",
        "STKS", "NHS", "Healing Biologics",
    ],
}
_ACTION_OPTIONS = {
    "AR": [
        "Report Extraction", "Claim Submission", "Claim Correction", "Claim Resubmission",
        "Claim Reconsideration", "Void Claim", "Replacement Claim", "Secondary/Tertiary Billing",
        "Claim Rejection", "Denial Review", "Denial Correction", "Denial Resubmission",
        "Denial Appeal", "Appeal", "Peer-to-Peer Review", "Medical Necessity Appeal",
        "Coding Appeal", "Authorization Appeal", "Claim Status Follow-up", "Payer Call",
        "Payer Portal Follow-up", "Pending Claim Follow-up", "Medical Records Request",
        "Documentation Submission", "Payer Escalation", "Payment Posting",
        "Underpayment Review", "Underpayment Appeal", "Contractual Adjustment",
        "Payment Variance Review", "Recoupment Review", "Refund Processing",
        "Eligibility Verification", "Insurance Discovery", "Insurance Update", "COB Update",
        "Payer Correction", "Subscriber Information Correction", "Authorization Verification",
        "Patient Balance Review", "Patient Statement Generation", "Patient Billing",
        "Patient Payment Follow-up", "Payment Plan Setup", "Financial Assistance Review",
        "Patient Responsibility Adjustment", "Bad Debt Transfer", "Write-off",
        "Small Balance Write-off", "Administrative Adjustment", "Charity Care Adjustment",
        "Bad Debt", "Account Closure", "Zero Balance Resolution",
        "Duplicate Account/Claim Resolution", "Credit Balance Resolution",
    ],
    "EV": ["Portal Verification", "Note Posting", "Report Extraction"],
}
_EHR_OPTIONS = {
    "AR": ["HST", "eCW", "EXA", "Netsmart", "Modmed", "Brightree", "Experity", "Cerner", "Athena"],
    "EV": ["eCW", "Brightree", "Exa", "Experity", "Cerner", "Healing Biologix"],
}


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "form_options",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("lob", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "lob", "category", "label"),
    )

    bind = op.get_bind()
    org_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM organizations")).fetchall()]
    form_options_table = sa.table(
        "form_options",
        sa.column("id", sa.Uuid()), sa.column("org_id", sa.Uuid()),
        sa.column("lob", sa.String()), sa.column("category", sa.String()),
        sa.column("label", sa.String()), sa.column("active", sa.Boolean()),
    )
    for org_id in org_ids:
        rows = []
        for category, options_by_lob in (
            ("client", _CLIENT_OPTIONS), ("action", _ACTION_OPTIONS), ("ehr", _EHR_OPTIONS),
        ):
            for lob, labels in options_by_lob.items():
                rows.extend(
                    {"id": uuid.uuid4(), "org_id": org_id, "lob": lob, "category": category, "label": label, "active": True}
                    for label in labels
                )
        bind.execute(form_options_table.insert(), rows)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("form_options")
```

- [ ] **Step 4: Run the migration**

```bash
cd engine && source .venv/bin/activate
alembic upgrade head
```

Expected: no errors.

- [ ] **Step 5: Manually verify the seed against the dev database**

The `atlas_test` DB used by `pytest` is created empty and has migrations applied to it *before* any test creates an organization — so the migration's `SELECT id FROM organizations` seed loop always finds zero rows there, and there is nothing meaningful to assert about seeding in the pytest suite. Verify it instead against the local dev DB (the one `alembic upgrade head` was run against in Step 4), which already has a real org:

```bash
cd engine && source .venv/bin/activate
python -c "
from atlas_engine.db import session_scope
from atlas_engine.db.models import FormOption, Organization
with session_scope() as s:
    for org in s.query(Organization).all():
        counts = {}
        for row in s.query(FormOption).filter_by(org_id=org.id).all():
            counts[row.category] = counts.get(row.category, 0) + 1
        print(org.slug, counts)
"
```

Expected output: every existing org (including `atlas-dev`) prints `{'client': 26, 'action': 59, 'ehr': 15}` (26 = 15 AR + 11 EV client labels, 59 = 56 AR + 3 EV action labels, 15 = 9 AR + 6 EV ehr labels — matching the counts of `_CLIENT_OPTIONS`/`_ACTION_OPTIONS`/`_EHR_OPTIONS` in the migration). If any org is missing a category or the counts don't match, the migration's seed loop didn't run correctly — re-check Step 3 before moving on.

- [ ] **Step 6: Add a DB-level unit test for the table itself**

This runs against the empty `atlas_test` DB, so it creates its own org and rows directly (it is not a seeding check — it only proves the table, model, and unique constraint behave correctly, which the seed step above already exercised manually). Append to `engine/tests/test_recording_service.py` (after the existing `test_master_data_list_functions_return_only_active_rows_for_the_org` function):

```python
def test_form_option_model_enforces_uniqueness_per_org_lob_category_label(fixture_portal_url):
    from sqlalchemy.exc import IntegrityError
    from atlas_engine.db.models import FormOption

    with session_scope() as session:
        org = Organization(name="Form Option Model Org", slug=f"form-option-model-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        session.add(FormOption(org_id=org.id, lob="AR", category="action", label="Claim Submission"))
        session.flush()
        org_id = org.id

    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(FormOption(org_id=org_id, lob="AR", category="action", label="Claim Submission"))
            session.flush()
```

Check whether `pytest` is already imported at the top of `engine/tests/test_recording_service.py` — if not, add `import pytest` alongside the existing imports.

- [ ] **Step 7: Run the test**

```bash
cd engine && source .venv/bin/activate
pytest tests/test_recording_service.py -k form_option -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add engine/src/atlas_engine/db/models/masterdata.py engine/src/atlas_engine/db/models/__init__.py engine/migrations/versions/ engine/tests/test_recording_service.py
git commit -m "feat: add form_options table, seeded from today's hardcoded dropdown lists"
```

---

### Task 2: Service functions

**Files:**
- Modify: `engine/src/atlas_engine/recording_service.py` (append after `list_carc_codes`, currently ending at line 754)
- Test: `engine/tests/test_recording_service.py`

**Interfaces:**
- Consumes: `FormOption` (Task 1), `session_scope` (already imported in this file).
- Produces:
  - `list_form_options(org_id: str, lob: str, category: str, *, include_inactive: bool = False) -> list[dict]` — each dict: `{"id": str, "label": str, "active": bool}`, ordered by `label`.
  - `create_form_option(org_id: str, lob: str, category: str, label: str) -> dict` — returns `{"id": str, "label": str, "active": bool}`. Raises `sqlalchemy.exc.IntegrityError` on a duplicate `(org_id, lob, category, label)` (caller catches it).
  - `update_form_option(form_option_id: str, *, label: str | None = None, active: bool | None = None) -> dict` — "not None means set" convention (matches `update_recording_details`, `recording_service.py:507`). Raises `ValueError` if the id doesn't exist.

- [ ] **Step 1: Write the failing tests**

Append to `engine/tests/test_recording_service.py`:

```python
def test_list_form_options_filters_by_lob_category_org_and_active(fixture_portal_url):
    from atlas_engine.db.models import FormOption
    from atlas_engine.recording_service import list_form_options

    with session_scope() as session:
        org = Organization(name="Form Options Org", slug=f"form-options-org-{uuid.uuid4()}")
        other_org = Organization(name="Other Form Options Org", slug=f"other-form-options-org-{uuid.uuid4()}")
        session.add_all([org, other_org])
        session.flush()
        org_id = str(org.id)
        session.add_all([
            FormOption(org_id=org.id, lob="AR", category="action", label="Active AR Action", active=True),
            FormOption(org_id=org.id, lob="AR", category="action", label="Inactive AR Action", active=False),
            FormOption(org_id=org.id, lob="EV", category="action", label="EV Action", active=True),
            FormOption(org_id=org.id, lob="AR", category="client", label="AR Client", active=True),
            FormOption(org_id=other_org.id, lob="AR", category="action", label="Other Org Action", active=True),
        ])

    assert [o["label"] for o in list_form_options(org_id, "AR", "action")] == ["Active AR Action"]
    all_ar_actions = list_form_options(org_id, "AR", "action", include_inactive=True)
    assert sorted(o["label"] for o in all_ar_actions) == ["Active AR Action", "Inactive AR Action"]
    assert [o["label"] for o in list_form_options(org_id, "EV", "action")] == ["EV Action"]
    assert [o["label"] for o in list_form_options(org_id, "AR", "client")] == ["AR Client"]


def test_create_form_option_raises_integrity_error_on_duplicate(fixture_portal_url):
    from sqlalchemy.exc import IntegrityError
    from atlas_engine.recording_service import create_form_option

    with session_scope() as session:
        org = Organization(name="Create Form Option Org", slug=f"create-form-option-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    created = create_form_option(org_id, "AR", "action", "New Action")
    assert created["label"] == "New Action"
    assert created["active"] is True

    with pytest.raises(IntegrityError):
        create_form_option(org_id, "AR", "action", "New Action")


def test_update_form_option_sets_only_provided_fields(fixture_portal_url):
    from atlas_engine.recording_service import create_form_option, list_form_options, update_form_option

    with session_scope() as session:
        org = Organization(name="Update Form Option Org", slug=f"update-form-option-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    created = create_form_option(org_id, "AR", "action", "Original Label")

    updated = update_form_option(created["id"], active=False)
    assert updated["label"] == "Original Label"
    assert updated["active"] is False
    assert [o["label"] for o in list_form_options(org_id, "AR", "action")] == []

    renamed = update_form_option(created["id"], label="Renamed Action")
    assert renamed["label"] == "Renamed Action"
    assert renamed["active"] is False  # untouched by this call
```

Check whether `pytest` is already imported at the top of `engine/tests/test_recording_service.py` — if not, add `import pytest` alongside the existing imports.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd engine && source .venv/bin/activate
pytest tests/test_recording_service.py -k "form_option" -v
```

Expected: FAIL with `ImportError` / `cannot import name 'list_form_options'`.

- [ ] **Step 3: Implement the service functions**

Append to `engine/src/atlas_engine/recording_service.py` (after `list_carc_codes`, which currently ends at line 754):

```python
def list_form_options(org_id: str, lob: str, category: str, *, include_inactive: bool = False) -> list[dict]:
    from atlas_engine.db.models import FormOption

    with session_scope() as session:
        query = session.query(FormOption).filter_by(org_id=uuid.UUID(org_id), lob=lob, category=category)
        if not include_inactive:
            query = query.filter_by(active=True)
        rows = query.order_by(FormOption.label).all()
        return [{"id": str(r.id), "label": r.label, "active": r.active} for r in rows]


def create_form_option(org_id: str, lob: str, category: str, label: str) -> dict:
    from atlas_engine.db.models import FormOption

    with session_scope() as session:
        option = FormOption(org_id=uuid.UUID(org_id), lob=lob, category=category, label=label)
        session.add(option)
        session.flush()
        return {"id": str(option.id), "label": option.label, "active": option.active}


def update_form_option(form_option_id: str, *, label: str | None = None, active: bool | None = None) -> dict:
    from atlas_engine.db.models import FormOption

    with session_scope() as session:
        option = session.get(FormOption, uuid.UUID(form_option_id))
        if option is None:
            raise ValueError(f"No such form option: {form_option_id}")
        if label is not None:
            option.label = label
        if active is not None:
            option.active = active
        session.flush()
        return {"id": str(option.id), "label": option.label, "active": option.active}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd engine && source .venv/bin/activate
pytest tests/test_recording_service.py -k "form_option" -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/recording_service.py engine/tests/test_recording_service.py
git commit -m "feat: add list/create/update service functions for form options"
```

---

### Task 3: API endpoints

**Files:**
- Modify: `engine/src/atlas_engine/api.py` (add imports near line 29 where `list_systems` etc. would be imported from `recording_service`, and add endpoints after `list_carc_codes_endpoint`, currently ending at line 289)
- Test: Create `engine/tests/test_api_form_options.py`

**Interfaces:**
- Consumes: `list_form_options`, `create_form_option`, `update_form_option` (Task 2); `CurrentUser`, `get_current_user`, `require_job_function` (already imported in `api.py`).
- Produces: `GET /form-options`, `GET /form-options/all`, `POST /form-options`, `PATCH /form-options/{form_option_id}`.

- [ ] **Step 1: Check the import**

Find where `recording_service` functions are imported into `api.py` (search for `list_systems` — it's one of several names imported via a `from atlas_engine.recording_service import (...)` block). Add `create_form_option`, `list_form_options`, `update_form_option` to that same import block, keeping the existing alphabetical-ish grouping style.

- [ ] **Step 2: Write the failing tests**

Create `engine/tests/test_api_form_options.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd engine && source .venv/bin/activate
pytest tests/test_api_form_options.py -v
```

Expected: FAIL (404s — routes don't exist yet).

- [ ] **Step 4: Implement the endpoints**

Add to `engine/src/atlas_engine/api.py`, immediately after `list_carc_codes_endpoint` (currently ending at line 289):

```python
class CreateFormOptionBody(BaseModel):
    lob: str
    category: str
    label: str


class UpdateFormOptionBody(BaseModel):
    label: str | None = None
    active: bool | None = None


@app.get("/form-options")
def list_form_options_endpoint(
    lob: str, category: str, _user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    return list_form_options(str(_user.org_id), lob, category)


@app.get("/form-options/all")
def list_all_form_options_endpoint(
    lob: str, category: str, _user: CurrentUser = Depends(require_job_function()),
) -> list[dict]:
    return list_form_options(str(_user.org_id), lob, category, include_inactive=True)


@app.post("/form-options")
def create_form_option_endpoint(
    body: CreateFormOptionBody, _user: CurrentUser = Depends(require_job_function()),
) -> dict:
    try:
        return create_form_option(str(_user.org_id), body.lob, body.category, body.label)
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f'"{body.label}" already exists for {body.lob}/{body.category}')


@app.patch("/form-options/{form_option_id}")
def update_form_option_endpoint(
    form_option_id: str, body: UpdateFormOptionBody, _user: CurrentUser = Depends(require_job_function()),
) -> dict:
    return update_form_option(form_option_id, label=body.label, active=body.active)
```

`require_job_function()` called with no arguments means no `job_function` value satisfies it, so only `role in ("owner", "admin")` passes (see `auth/deps.py:36-41`) — this is the existing convention, not a new mechanism.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd engine && source .venv/bin/activate
pytest tests/test_api_form_options.py -v
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
git add engine/src/atlas_engine/api.py engine/tests/test_api_form_options.py
git commit -m "feat: add /form-options endpoints, owner/admin gated for mutations"
```

---

### Task 4: Frontend data hooks

**Files:**
- Create: `frontend/src/lib/formOptions.ts`
- Test: Create `frontend/src/lib/formOptions.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` (`frontend/src/lib/api.ts:12`).
- Produces:
  - `interface FormOption { id: string; label: string; active: boolean }`
  - `useFormOptions(lob: string, category: 'client' | 'action' | 'ehr')` → `useQuery` result of `FormOption[]` (active only).
  - `useAllFormOptions(lob: string, category: 'client' | 'action' | 'ehr')` → `useQuery` result of `FormOption[]` (including inactive).
  - `useCreateFormOption()` → `useMutation` taking `{ lob: string; category: string; label: string }`, invalidates both the above query keys.
  - `useUpdateFormOption()` → `useMutation` taking `{ id: string; label?: string; active?: boolean }`, invalidates both the above query keys.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/formOptions.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { useAllFormOptions, useCreateFormOption, useFormOptions, useUpdateFormOption } from './formOptions'

let queryClient: QueryClient

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

describe('useFormOptions', () => {
  it('fetches active options for a lob/category', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/form-options?lob=AR&category=action') {
        return Promise.resolve({ ok: true, status: 200, json: async () => [{ id: '1', label: 'Claim Submission', active: true }] })
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    const { result } = renderHook(() => useFormOptions('AR', 'action'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([{ id: '1', label: 'Claim Submission', active: true }])
  })
})

describe('useAllFormOptions', () => {
  it('fetches from the /all endpoint', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/form-options/all?lob=AR&category=action') {
        return Promise.resolve({
          ok: true, status: 200,
          json: async () => [{ id: '1', label: 'Active', active: true }, { id: '2', label: 'Inactive', active: false }],
        })
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    const { result } = renderHook(() => useAllFormOptions('AR', 'action'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toHaveLength(2)
  })
})

describe('useCreateFormOption', () => {
  it('POSTs the new option', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/form-options' && init?.method === 'POST') {
        const body = JSON.parse(init.body as string)
        expect(body).toEqual({ lob: 'AR', category: 'action', label: 'New Action' })
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ id: '3', label: 'New Action', active: true }) })
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    const { result } = renderHook(() => useCreateFormOption(), { wrapper })
    result.current.mutate({ lob: 'AR', category: 'action', label: 'New Action' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
  })
})

describe('useUpdateFormOption', () => {
  it('PATCHes the option', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/form-options/3' && init?.method === 'PATCH') {
        const body = JSON.parse(init.body as string)
        expect(body).toEqual({ active: false })
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ id: '3', label: 'New Action', active: false }) })
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    const { result } = renderHook(() => useUpdateFormOption(), { wrapper })
    result.current.mutate({ id: '3', active: false })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend && npm test -- formOptions.test.tsx
```

Expected: FAIL (`Failed to resolve import "./formOptions"`).

- [ ] **Step 3: Implement the hooks**

Create `frontend/src/lib/formOptions.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface FormOption {
  id: string
  label: string
  active: boolean
}

export type FormOptionCategory = 'client' | 'action' | 'ehr'

function queryKey(suffix: 'active' | 'all', lob: string, category: FormOptionCategory) {
  return ['form-options', suffix, lob, category] as const
}

export function useFormOptions(lob: string, category: FormOptionCategory) {
  return useQuery({
    queryKey: queryKey('active', lob, category),
    queryFn: () => apiFetch<FormOption[]>(`/form-options?lob=${lob}&category=${category}`),
  })
}

export function useAllFormOptions(lob: string, category: FormOptionCategory) {
  return useQuery({
    queryKey: queryKey('all', lob, category),
    queryFn: () => apiFetch<FormOption[]>(`/form-options/all?lob=${lob}&category=${category}`),
  })
}

export function useCreateFormOption() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { lob: string; category: FormOptionCategory; label: string }) =>
      apiFetch<FormOption>('/form-options', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['form-options'] }),
  })
}

export function useUpdateFormOption() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; label?: string; active?: boolean }) =>
      apiFetch<FormOption>(`/form-options/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['form-options'] }),
  })
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd frontend && npm test -- formOptions.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/formOptions.ts frontend/src/lib/formOptions.test.tsx
git commit -m "feat: add React Query hooks for form options"
```

---

### Task 5: Wire the recording form to the new hooks

**Files:**
- Modify: `frontend/src/lib/recordingFormSchema.ts` (remove `CLIENT_OPTIONS`, `ACTION_OPTIONS`, `EHR_OPTIONS`)
- Modify: `frontend/src/components/RecordingFormSectionA.tsx`
- Test: Create `frontend/src/components/RecordingFormSectionA.test.tsx`

**Interfaces:**
- Consumes: `useFormOptions` (Task 4).

- [ ] **Step 1: Remove the hardcoded constants**

In `frontend/src/lib/recordingFormSchema.ts`, delete the `CLIENT_OPTIONS`, `ACTION_OPTIONS`, and `EHR_OPTIONS` exports (lines 19-50, 56-120, 126-146 as currently numbered) along with their section-comment headers. Leave `Lob`, `LOB_TABS`, `SPECIFICITY_OPTIONS`, and everything from "Section B" onward untouched.

- [ ] **Step 2: Write the failing test**

Create `frontend/src/components/RecordingFormSectionA.test.tsx`:

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
    if (path === '/form-options?lob=AR&category=action') return Promise.resolve(jsonResponse([{ id: '2', label: 'Claim Submission', active: true }]))
    if (path === '/form-options?lob=AR&category=ehr') return Promise.resolve(jsonResponse([{ id: '3', label: 'eCW', active: true }]))
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

describe('RecordingFormSectionA', () => {
  it('loads Client options from the API for the active LOB', async () => {
    renderForm()
    fireEvent.click(screen.getByText('Select Client'))
    await waitFor(() => expect(screen.getByText('Acme Clinic')).toBeInTheDocument())
  })
})
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd frontend && npm test -- RecordingFormSectionA.test.tsx
```

Expected: FAIL (options list empty — still reading the now-deleted hardcoded constants, or a compile error since the import no longer exists).

- [ ] **Step 4: Wire the component to the hooks**

In `frontend/src/components/RecordingFormSectionA.tsx`:

Replace the import block (lines 3-6):

```ts
import {
  ACTION_OPTIONS, CLIENT_OPTIONS, EHR_OPTIONS, LOB_TABS,
  SPECIFICITY_OPTIONS, suggestWorkflowName, type Lob,
} from '../lib/recordingFormSchema'
```

with:

```ts
import { LOB_TABS, SPECIFICITY_OPTIONS, suggestWorkflowName, type Lob } from '../lib/recordingFormSchema'
import { useFormOptions } from '../lib/formOptions'
```

Inside `RecordingFormSectionA` (after the existing `useState` calls, before the `useEffect`), add:

```ts
  const clientOptions = useFormOptions(lob, 'client').data ?? []
  const actionOptions = useFormOptions(lob, 'action').data ?? []
  const ehrOptions = useFormOptions(lob, 'ehr').data ?? []
```

Update the three `SearchableSelect` call sites to use `.label` strings from these queries instead of the old constants:

```tsx
          <SearchableSelect
            label="Select Client"
            options={clientOptions.map((o) => o.label)}
            value={client}
            onChange={setClient}
            placeholder="Type to search…"
          />
```

(and the equivalent change for `Select Action` → `actionOptions.map((o) => o.label)`, `Select EHR` → `ehrOptions.map((o) => o.label)`).

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd frontend && npm test -- RecordingFormSectionA.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Run the full frontend test suite**

```bash
cd frontend && npm test
```

Expected: PASS (no regressions — check in particular that `recordingFormSchema.test.ts` still passes, since it never referenced the deleted constants).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/recordingFormSchema.ts frontend/src/components/RecordingFormSectionA.tsx frontend/src/components/RecordingFormSectionA.test.tsx
git commit -m "feat: load recording-form Client/Action/EHR options from the API"
```

---

### Task 6: Admin page for managing options

**Files:**
- Create: `frontend/src/auth/RequireAdminAccess.tsx`
- Test: Create `frontend/src/auth/RequireAdminAccess.test.tsx`
- Create: `frontend/src/pages/dev/FormOptionsPage.tsx`
- Test: Create `frontend/src/pages/dev/FormOptionsPage.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/layout/AppLayout.tsx`

**Interfaces:**
- Consumes: `useAllFormOptions`, `useCreateFormOption`, `useUpdateFormOption` (Task 4); `useAuth` (`frontend/src/auth/AuthContext.tsx`); `LOB_TABS` (`recordingFormSchema.ts`); `FormOptionCategory` (Task 4).

- [ ] **Step 1: Write the failing test for the route guard**

Create `frontend/src/auth/RequireAdminAccess.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from './AuthContext'
import { ProtectedRoute } from './ProtectedRoute'
import { RequireAdminAccess } from './RequireAdminAccess'

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

let role = 'member'

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') {
      return Promise.resolve(jsonResponse({ user_id: 'u-1', org_id: 'org-1', email: 'x@x.com', role, job_function: 'developer' }))
    }
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderAtFormOptions() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/dev/form-options']}>
        <AuthProvider>
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route element={<RequireAdminAccess />}>
                <Route path="/dev/form-options" element={<div>Form options content</div>} />
              </Route>
              <Route path="/dev" element={<div>Dev library content</div>} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('RequireAdminAccess', () => {
  it('redirects a member away', async () => {
    role = 'member'
    renderAtFormOptions()
    expect(await screen.findByText('Dev library content')).toBeInTheDocument()
    expect(screen.queryByText('Form options content')).toBeNull()
  })

  it('lets an owner through', async () => {
    role = 'owner'
    renderAtFormOptions()
    expect(await screen.findByText('Form options content')).toBeInTheDocument()
  })

  it('lets an admin through', async () => {
    role = 'admin'
    renderAtFormOptions()
    expect(await screen.findByText('Form options content')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend && npm test -- RequireAdminAccess.test.tsx
```

Expected: FAIL (`Failed to resolve import "./RequireAdminAccess"`).

- [ ] **Step 3: Implement the guard**

Create `frontend/src/auth/RequireAdminAccess.tsx`:

```tsx
import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from './AuthContext'

// Guards /dev/form-options itself, not just its nav link — gated on role
// (owner/admin), not job_function, matching the backend's
// require_job_function() with no arguments (api.py's /form-options
// mutation endpoints).
export function RequireAdminAccess() {
  const { user } = useAuth()
  if (user && user.role !== 'owner' && user.role !== 'admin') return <Navigate to="/dev" replace />
  return <Outlet />
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd frontend && npm test -- RequireAdminAccess.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Write the failing test for the admin page**

Create `frontend/src/pages/dev/FormOptionsPage.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { FormOptionsPage } from './FormOptionsPage'

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

let options = [{ id: '1', label: 'Claim Submission', active: true }]

beforeEach(() => {
  options = [{ id: '1', label: 'Claim Submission', active: true }]
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
})
```

- [ ] **Step 6: Run the test to verify it fails**

```bash
cd frontend && npm test -- FormOptionsPage.test.tsx
```

Expected: FAIL (`Failed to resolve import "./FormOptionsPage"`).

- [ ] **Step 7: Implement the admin page**

Create `frontend/src/pages/dev/FormOptionsPage.tsx`:

```tsx
import { useState } from 'react'
import { LOB_TABS, type Lob } from '../../lib/recordingFormSchema'
import { useAllFormOptions, useCreateFormOption, useUpdateFormOption, type FormOptionCategory } from '../../lib/formOptions'

const CATEGORY_TABS: { category: FormOptionCategory; label: string }[] = [
  { category: 'client', label: 'Client' },
  { category: 'action', label: 'Action' },
  { category: 'ehr', label: 'EHR' },
]

export function FormOptionsPage() {
  const [lob, setLob] = useState<Lob>('AR')
  const [category, setCategory] = useState<FormOptionCategory>('action')
  const [newLabel, setNewLabel] = useState('')

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
          </tr>
        </thead>
        <tbody>
          {options.map((option) => (
            <tr key={option.id} className="border-t border-atlas-border">
              <td className="px-3 py-2">{option.label}</td>
              <td className="px-3 py-2">
                <input
                  type="checkbox"
                  aria-label={`Deactivate ${option.label}`}
                  checked={option.active}
                  onChange={(e) => updateOption.mutate({ id: option.id, active: e.target.checked })}
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

- [ ] **Step 8: Run the test to verify it passes**

```bash
cd frontend && npm test -- FormOptionsPage.test.tsx
```

Expected: PASS.

- [ ] **Step 9: Wire the route and nav link**

In `frontend/src/App.tsx`, add the import (after the other `./pages/dev/*` imports, line 12):

```ts
import { FormOptionsPage } from './pages/dev/FormOptionsPage'
import { RequireAdminAccess } from './auth/RequireAdminAccess'
```

Add the route inside the existing `<Route element={<RequireDeveloperAccess />}>` block (after `/dev/costs`, before `/dev/recordings/:recordingId`):

```tsx
            <Route element={<RequireAdminAccess />}>
              <Route path="/dev/form-options" element={<FormOptionsPage />} />
            </Route>
```

In `frontend/src/layout/AppLayout.tsx`, change the `dev` array in the `NAV` constant (lines 9-14) from:

```ts
  dev: [
    { to: '/dev', label: 'Recording library' },
    { to: '/dev/requests', label: 'Request queue' },
    { to: '/dev/costs', label: 'Cost dashboard' },
    { to: '/approvals', label: 'Heal approvals' },
  ],
```

to:

```ts
  dev: [
    { to: '/dev', label: 'Recording library' },
    { to: '/dev/requests', label: 'Request queue' },
    { to: '/dev/costs', label: 'Cost dashboard' },
    { to: '/approvals', label: 'Heal approvals' },
    { to: '/dev/form-options', label: 'Form options' },
  ],
```

Then, in the `AppLayout` component body, filter that last entry out for non-owner/admin users. Change:

```tsx
          {NAV[role].map((item) => (
```

to:

```tsx
          {NAV[role]
            .filter((item) => item.to !== '/dev/form-options' || user?.role === 'owner' || user?.role === 'admin')
            .map((item) => (
```

(and correspondingly close the added `.filter(...)` call's parens before `.map`'s own closing — match the existing JSX structure; the `))}` that currently closes `.map` stays as-is).

- [ ] **Step 10: Manually verify in the browser**

```bash
cd frontend && npm run dev
```

Log in as an owner/admin user, navigate to the sidebar's "Form options" link (or `/dev/form-options` directly), confirm the table loads, add an option, toggle it off, and confirm it disappears from the "Start new recording" modal's corresponding dropdown. Then log in as a plain `member` and confirm the nav link is gone and the URL redirects to `/dev`.

- [ ] **Step 11: Run the full frontend test suite**

```bash
cd frontend && npm test
```

Expected: PASS (no regressions — check `AppLayout.test.tsx` and `App.test.tsx` in particular, since both files changed).

- [ ] **Step 12: Commit**

```bash
git add frontend/src/auth/RequireAdminAccess.tsx frontend/src/auth/RequireAdminAccess.test.tsx frontend/src/pages/dev/FormOptionsPage.tsx frontend/src/pages/dev/FormOptionsPage.test.tsx frontend/src/App.tsx frontend/src/layout/AppLayout.tsx
git commit -m "feat: add owner/admin-only Form options admin page"
```
