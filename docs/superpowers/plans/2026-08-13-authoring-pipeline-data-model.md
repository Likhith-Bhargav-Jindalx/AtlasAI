# Authoring Pipeline Data Model & Migrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the database tables and enum values the authoring pipeline rebuild needs — `AuthoringRun`, `AuthoringRunEvent`, `CertificationRun`, `PageExtraction`, and a `partially_certified` value on `GoldenScript.status` — with no behavior changes yet; this plan is pure schema.

**Architecture:** Four new SQLAlchemy models following the exact conventions already used in `engine/src/atlas_engine/db/models/` (declarative `Base`, `Mapped`/`mapped_column`, `PortableJSON` for JSON columns, `Enum(*TUPLE, name="...")` for Postgres enums), one Alembic migration generated via autogenerate and hand-verified, applied to both the `atlas` and `atlas_test` databases.

**Tech Stack:** Python 3.14, SQLAlchemy 2.x declarative models, Alembic migrations, Postgres 5434 (`atlas`/`atlas_test`), pytest with an in-memory SQLite structural-test pattern already used in `engine/tests/test_db_models.py`.

**Spec:** `docs/superpowers/specs/2026-08-13-authoring-pipeline-rebuild-design.md`

## Global Constraints

- Follow the exact model conventions in `engine/src/atlas_engine/db/models/scripts.py` and `video.py` (this plan's closest precedents) — `Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)` for PKs, `ForeignKey(..., ondelete=...)` on every FK, `server_default=func.now()` for `created_at`.
- New columns added to an existing table via migration must have a `server_default` if `nullable=False`, exactly like `213ecb56cdce_add_page_index_to_video_assets.py` did for `video_assets.page_index`.
- Adding a value to an existing Postgres `ENUM` type requires `ALTER TYPE ... ADD VALUE` run in an autocommit block — it cannot run inside Alembic's normal transactional migration context.
- Migrations apply to both databases: `.venv/bin/alembic upgrade head` (uses `atlas`, the default in `config.py`) and `ATLAS_DATABASE_URL=postgresql+psycopg://atlas:atlas_dev_password@localhost:5434/atlas_test .venv/bin/alembic upgrade head` (matches `tests/conftest.py`'s test database).
- Structural model tests use the in-memory SQLite pattern from `engine/tests/test_db_models.py` (`create_engine("sqlite:///:memory:")`, `Base.metadata.create_all(engine)`) — these don't exercise Postgres-specific ENUM/JSONB behavior, only relationships/cascades/constraints.

---

### Task 1: `AuthoringRun` model

**Files:**
- Create: `engine/src/atlas_engine/db/models/authoring.py`
- Modify: `engine/src/atlas_engine/db/models/__init__.py`
- Test: `engine/tests/test_db_models_authoring.py`

**Interfaces:**
- Produces: `AuthoringRun` class with columns `id, org_id, recording_id, golden_script_id, status, current_phase, repair_attempt, created_at, ended_at`; constant tuples `AUTHORING_RUN_STATUSES = ("running", "waiting_for_developer", "completed", "failed", "cancelled")` and `AUTHORING_RUN_PHASES = ("replay", "extract", "brain", "golden", "certifying", "done")`, both exported from `authoring.py`.

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/test_db_models_authoring.py
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas_engine.db import Base
from atlas_engine.db.models import AuthoringRun, GoldenScript, Organization, PortalProfile, Recording


def _sqlite_engine():
    return create_engine("sqlite:///:memory:")


def test_authoring_run_links_recording_and_golden_script():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Anka Health", slug="anka-health-authoring")
        profile = PortalProfile(org=org, domain="portal.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/1/",
        )
        run = AuthoringRun(
            org=org, recording=recording, status="running", current_phase="replay",
        )
        session.add_all([org, profile, recording, run])
        session.commit()

        assert run.id is not None
        assert run.recording.id == recording.id
        assert run.golden_script_id is None
        assert run.repair_attempt == 0

        script = GoldenScript(org=org, portal_profile=profile, version=1, script_blob_ref="v1.py")
        session.add(script)
        session.flush()
        run.golden_script = script
        session.commit()

        assert run.golden_script.id == script.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && .venv/bin/pytest tests/test_db_models_authoring.py -v`
Expected: FAIL — `ImportError: cannot import name 'AuthoringRun'`

- [ ] **Step 3: Write the model**

```python
# engine/src/atlas_engine/db/models/authoring.py
import uuid
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atlas_engine.db import Base

AUTHORING_RUN_STATUSES = ("running", "waiting_for_developer", "completed", "failed", "cancelled")
AUTHORING_RUN_PHASES = ("replay", "extract", "brain", "golden", "certifying", "done")


class AuthoringRun(Base):
    __tablename__ = "authoring_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    golden_script_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("golden_scripts.id", ondelete="SET NULL"), default=None
    )
    status: Mapped[str] = mapped_column(Enum(*AUTHORING_RUN_STATUSES, name="authoring_run_status"), default="running")
    current_phase: Mapped[str] = mapped_column(Enum(*AUTHORING_RUN_PHASES, name="authoring_run_phase"), default="replay")
    repair_attempt: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(default=None)

    org: Mapped["Organization"] = relationship()
    recording: Mapped["Recording"] = relationship()
    golden_script: Mapped["GoldenScript | None"] = relationship()
```

- [ ] **Step 4: Wire up the export**

In `engine/src/atlas_engine/db/models/__init__.py`, add the import and `__all__` entry:

```python
from atlas_engine.db.models.authoring import AuthoringRun
```

Add `"AuthoringRun"` to the `__all__` list, keeping it alphabetically sorted alongside the existing entries.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd engine && .venv/bin/pytest tests/test_db_models_authoring.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/db/models/authoring.py engine/src/atlas_engine/db/models/__init__.py engine/tests/test_db_models_authoring.py
git commit -m "feat: add AuthoringRun model"
```

---

### Task 2: `AuthoringRunEvent` model

**Files:**
- Modify: `engine/src/atlas_engine/db/models/authoring.py`
- Modify: `engine/src/atlas_engine/db/models/__init__.py`
- Test: `engine/tests/test_db_models_authoring.py`

**Interfaces:**
- Consumes: `AuthoringRun` from Task 1.
- Produces: `AuthoringRunEvent` class with columns `id, authoring_run_id, timestamp, phase, step_index, level, message`; constant `AUTHORING_RUN_EVENT_LEVELS = ("info", "warning", "error")`.

- [ ] **Step 1: Write the failing test**

Add to `engine/tests/test_db_models_authoring.py`:

```python
from atlas_engine.db.models import AuthoringRunEvent


def test_authoring_run_event_belongs_to_run():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Anka Health", slug="anka-health-authoring-events")
        profile = PortalProfile(org=org, domain="portal-events.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/2/",
        )
        run = AuthoringRun(org=org, recording=recording, status="running", current_phase="replay")
        session.add_all([org, profile, recording, run])
        session.flush()

        event = AuthoringRunEvent(
            authoring_run=run, phase="replay", step_index=2, level="error", message="Step 2 failed: selector not found",
        )
        session.add(event)
        session.commit()

        assert event.id is not None
        assert event.authoring_run.id == run.id
        assert event.timestamp is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && .venv/bin/pytest tests/test_db_models_authoring.py -v -k event`
Expected: FAIL — `ImportError: cannot import name 'AuthoringRunEvent'`

- [ ] **Step 3: Add the model**

Append to `engine/src/atlas_engine/db/models/authoring.py`:

```python
AUTHORING_RUN_EVENT_LEVELS = ("info", "warning", "error")


class AuthoringRunEvent(Base):
    __tablename__ = "authoring_run_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    authoring_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("authoring_runs.id", ondelete="CASCADE"))
    timestamp: Mapped[datetime] = mapped_column(server_default=func.now())
    phase: Mapped[str] = mapped_column(Enum(*AUTHORING_RUN_PHASES, name="authoring_run_event_phase"))
    step_index: Mapped[int | None] = mapped_column(default=None)
    level: Mapped[str] = mapped_column(Enum(*AUTHORING_RUN_EVENT_LEVELS, name="authoring_run_event_level"), default="info")
    message: Mapped[str]

    authoring_run: Mapped["AuthoringRun"] = relationship()
```

- [ ] **Step 4: Wire up the export**

In `engine/src/atlas_engine/db/models/__init__.py`, add `AuthoringRunEvent` to the import from `atlas_engine.db.models.authoring` and to `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd engine && .venv/bin/pytest tests/test_db_models_authoring.py -v`
Expected: PASS (both tests)

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/db/models/authoring.py engine/src/atlas_engine/db/models/__init__.py engine/tests/test_db_models_authoring.py
git commit -m "feat: add AuthoringRunEvent model"
```

---

### Task 3: `CertificationRun` model

**Files:**
- Modify: `engine/src/atlas_engine/db/models/authoring.py`
- Modify: `engine/src/atlas_engine/db/models/__init__.py`
- Test: `engine/tests/test_db_models_authoring.py`

**Interfaces:**
- Consumes: `AuthoringRun` from Task 1.
- Produces: `CertificationRun` class with columns `id, authoring_run_id, run_number, status, failure_evidence_blob_ref`; constant `CERTIFICATION_RUN_STATUSES = ("pass", "fail")`.

- [ ] **Step 1: Write the failing test**

Add to `engine/tests/test_db_models_authoring.py`:

```python
from atlas_engine.db.models import CertificationRun


def test_certification_run_belongs_to_authoring_run():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Anka Health", slug="anka-health-cert-runs")
        profile = PortalProfile(org=org, domain="portal-cert.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/3/",
        )
        run = AuthoringRun(org=org, recording=recording, status="running", current_phase="certifying")
        session.add_all([org, profile, recording, run])
        session.flush()

        cert_1 = CertificationRun(authoring_run=run, run_number=1, status="pass")
        cert_2 = CertificationRun(authoring_run=run, run_number=2, status="fail", failure_evidence_blob_ref="orgs/x/certify/2/error.txt")
        session.add_all([cert_1, cert_2])
        session.commit()

        assert cert_1.id is not None
        assert cert_2.failure_evidence_blob_ref == "orgs/x/certify/2/error.txt"
        assert cert_1.authoring_run.id == run.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && .venv/bin/pytest tests/test_db_models_authoring.py -v -k certification`
Expected: FAIL — `ImportError: cannot import name 'CertificationRun'`

- [ ] **Step 3: Add the model**

Append to `engine/src/atlas_engine/db/models/authoring.py`:

```python
CERTIFICATION_RUN_STATUSES = ("pass", "fail")


class CertificationRun(Base):
    __tablename__ = "certification_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    authoring_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("authoring_runs.id", ondelete="CASCADE"))
    run_number: Mapped[int]
    status: Mapped[str] = mapped_column(Enum(*CERTIFICATION_RUN_STATUSES, name="certification_run_status"))
    failure_evidence_blob_ref: Mapped[str | None] = mapped_column(default=None)

    authoring_run: Mapped["AuthoringRun"] = relationship()
```

- [ ] **Step 4: Wire up the export**

In `engine/src/atlas_engine/db/models/__init__.py`, add `CertificationRun` to the import from `atlas_engine.db.models.authoring` and to `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd engine && .venv/bin/pytest tests/test_db_models_authoring.py -v`
Expected: PASS (all three tests)

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/db/models/authoring.py engine/src/atlas_engine/db/models/__init__.py engine/tests/test_db_models_authoring.py
git commit -m "feat: add CertificationRun model"
```

---

### Task 4: `GoldenScript.status` gains `partially_certified`

**Files:**
- Modify: `engine/src/atlas_engine/db/models/scripts.py`
- Test: `engine/tests/test_db_models.py`

**Interfaces:**
- Produces: `GOLDEN_SCRIPT_STATUSES = ("draft", "certified", "partially_certified", "active", "deprecated")`.

- [ ] **Step 1: Write the failing test**

Add to `engine/tests/test_db_models.py`:

```python
def test_golden_script_accepts_partially_certified_status():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Anka Health", slug="anka-health-partial-cert")
        profile = PortalProfile(org=org, domain="portal-partial.example.com")
        script = GoldenScript(
            org=org, portal_profile=profile, version=1, script_blob_ref="v1.py", status="partially_certified",
        )
        session.add_all([org, profile, script])
        session.commit()

        assert script.status == "partially_certified"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && .venv/bin/pytest tests/test_db_models.py -v -k partially_certified`
Expected: FAIL — SQLite doesn't enforce the Python-side `Enum` choices at the DB layer, but the column's Python-level `Enum` type in SQLAlchemy DOES validate against `GOLDEN_SCRIPT_STATUSES` on flush, so this fails with a `LookupError`/`StatementError` about an invalid enum value until the tuple is updated.

- [ ] **Step 3: Update the status tuple**

In `engine/src/atlas_engine/db/models/scripts.py`, change:

```python
GOLDEN_SCRIPT_STATUSES = ("draft", "certified", "active", "deprecated")
```

to:

```python
GOLDEN_SCRIPT_STATUSES = ("draft", "certified", "partially_certified", "active", "deprecated")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd engine && .venv/bin/pytest tests/test_db_models.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/db/models/scripts.py engine/tests/test_db_models.py
git commit -m "feat: add partially_certified to GoldenScript status"
```

---

### Task 5: `PageExtraction` model

**Files:**
- Modify: `engine/src/atlas_engine/db/models/portal.py`
- Modify: `engine/src/atlas_engine/db/models/__init__.py`
- Test: `engine/tests/test_db_models.py`

**Interfaces:**
- Produces: `PageExtraction` class with columns `id, recording_id, page_index, url, data, extracted_at`.

- [ ] **Step 1: Write the failing test**

Add to `engine/tests/test_db_models.py`:

```python
def test_page_extraction_belongs_to_recording():
    from atlas_engine.db.models import PageExtraction

    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Anka Health", slug="anka-health-extraction")
        profile = PortalProfile(org=org, domain="portal-extraction.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/4/",
        )
        session.add_all([org, profile, recording])
        session.flush()

        extraction = PageExtraction(
            recording=recording, page_index=0, url="https://portal-extraction.example.com/report",
            data={"headings": ["Patient Report"], "fields": {"Patient ID": "12345"}},
        )
        session.add(extraction)
        session.commit()

        assert extraction.id is not None
        assert extraction.recording.id == recording.id
        assert extraction.data["fields"]["Patient ID"] == "12345"
        assert extraction.extracted_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && .venv/bin/pytest tests/test_db_models.py -v -k page_extraction`
Expected: FAIL — `ImportError: cannot import name 'PageExtraction'`

- [ ] **Step 3: Add the model**

Append to `engine/src/atlas_engine/db/models/portal.py` (after the `ReferenceTrajectory` class, using `PortableJSON` already imported at the top of this file):

```python
class PageExtraction(Base):
    __tablename__ = "page_extractions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    page_index: Mapped[int] = mapped_column(default=0)
    url: Mapped[str]
    data: Mapped[dict] = mapped_column(PortableJSON)
    extracted_at: Mapped[datetime] = mapped_column(server_default=func.now())

    recording: Mapped["Recording"] = relationship()
```

- [ ] **Step 4: Wire up the export**

In `engine/src/atlas_engine/db/models/__init__.py`, add `PageExtraction` to the import from `atlas_engine.db.models.portal` and to `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd engine && .venv/bin/pytest tests/test_db_models.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/db/models/portal.py engine/src/atlas_engine/db/models/__init__.py engine/tests/test_db_models.py
git commit -m "feat: add PageExtraction model"
```

---

### Task 6: Generate and apply the migration

**Files:**
- Create: `engine/migrations/versions/<hash>_add_authoring_pipeline_tables.py`

**Interfaces:**
- Consumes: every model from Tasks 1-5.
- Produces: the `authoring_runs`, `authoring_run_events`, `certification_runs`, `page_extractions` tables and the `partially_certified` enum value on the live `atlas`/`atlas_test` Postgres databases.

- [ ] **Step 1: Generate the migration**

Run: `cd engine && .venv/bin/alembic revision --autogenerate -m "add authoring pipeline tables"`

This creates a new file under `engine/migrations/versions/`. Autogenerate detects new tables from `Base.metadata` (via `migrations/env.py`'s `from atlas_engine.db.models import *`) but it will **not** detect the `GOLDEN_SCRIPT_STATUSES` enum value addition — Alembic's autogenerate does not diff enum members. You must add that by hand in the next step.

- [ ] **Step 2: Add the enum-value migration by hand**

Open the generated file. It will contain `op.create_table(...)` calls for the four new tables (verify all four appear — `authoring_runs`, `authoring_run_events`, `certification_runs`, `page_extractions`). Add this to `upgrade()`, before the `create_table` calls (order doesn't matter here since it doesn't touch `golden_scripts` structurally, only its enum type):

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE golden_script_status ADD VALUE IF NOT EXISTS 'partially_certified'")

    # ... the autogenerated op.create_table(...) calls follow ...
```

And in `downgrade()`, Postgres cannot drop a single enum value — leave a comment rather than attempting it:

```python
def downgrade() -> None:
    # ... the autogenerated op.drop_table(...) calls ...

    # Postgres cannot drop a single value from an existing ENUM type;
    # 'partially_certified' is left in place on downgrade.
```

- [ ] **Step 3: Apply to the dev database**

Run: `cd engine && .venv/bin/alembic upgrade head`
Expected: no errors; the four new tables and the enum value now exist in `atlas`.

- [ ] **Step 4: Apply to the test database**

Run: `cd engine && ATLAS_DATABASE_URL=postgresql+psycopg://atlas:atlas_dev_password@localhost:5434/atlas_test .venv/bin/alembic upgrade head`
Expected: no errors; same result in `atlas_test`.

- [ ] **Step 5: Run the full backend test suite**

Run: `cd engine && .venv/bin/pytest -q`
Expected: all pass (this confirms the new tables/enum don't break anything already relying on the `golden_scripts` schema, and that every structural test from Tasks 1-5 still passes against the real Postgres-backed session fixtures used elsewhere in the suite).

- [ ] **Step 6: Commit**

```bash
git add engine/migrations/versions/
git commit -m "feat: migrate authoring pipeline tables and partially_certified status"
```
