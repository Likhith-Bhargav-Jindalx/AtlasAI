# Backend Production Hardening & Azure Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing `engine/` FastAPI app deployable and observable as the shared backend for tens of concurrent agent/developer clients — structured logging, a clean error boundary, upload-durable recording persistence, CI on every PR, and an Azure Container Apps deployment pipeline.

**Architecture:** No new services or apps. The existing `atlas_engine` FastAPI app gets a request-id-tagged JSON logging layer and a global exception handler, `recording_service.persist_recording` is restructured so a recording is a durable DB row (`status="capturing"` → `"captured"`/`"failed"`) from the moment it starts rather than only appearing once every upload succeeds, and Azure Blob uploads get retry-with-backoff. Packaging adds a `Dockerfile` (Playwright's official base image, migrations run at container startup) and two GitHub Actions workflows: `ci.yml` (tests gate every PR) and `deploy.yml` (build → push to ACR → deploy a new Container Apps revision on merge to `main`).

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 + Alembic, Postgres (psycopg3), pytest, GitHub Actions, Azure Container Apps / Container Registry / Database for PostgreSQL Flexible Server / Blob Storage.

**Spec:** `docs/superpowers/specs/2026-08-17-backend-production-deployment-design.md`

## Global Constraints

- Python `>=3.11` (`engine/pyproject.toml` `requires-python`).
- Dependency floors already pinned in `engine/pyproject.toml`: `fastapi>=0.115`, `uvicorn[standard]>=0.32`, `sqlalchemy>=2.0`, `alembic>=1.13`, `psycopg[binary]>=3.2`, `pydantic>=2.9`, `click>=8.1`, `bcrypt>=4.0`, `azure-storage-blob>=12.19`. Do not add new runtime dependencies unless a task below says to.
- Dev Postgres runs on port **5434**, not 5432 (`engine/README.md`).
- No test may require real Azure or LLM credentials or a real website — every existing test runs against local fixtures/fakes (`engine/README.md`); new tests must keep that property.
- `engine/tests/conftest.py` sets `ATLAS_DATABASE_URL`/`ATLAS_RUN_ROOT` at module scope before `atlas_engine` is imported — any new test module relying on different env vars must follow the same module-scope-before-import pattern, not `monkeypatch` inside a test function.
- `RECORDING_STATUSES = ("capturing", "captured", "failed", "discarded")` already exists in `engine/src/atlas_engine/db/models/portal.py:11` — no migration needed for Task 4.

---

### Task 1: Structured JSON logging + request-id middleware

**Files:**
- Create: `engine/src/atlas_engine/observability.py`
- Modify: `engine/src/atlas_engine/api.py:1-50` (add imports, call `configure_logging()`, register `RequestIdMiddleware`)
- Test: `engine/tests/test_observability.py`

**Interfaces:**
- Produces: `atlas_engine.observability.configure_logging(level: int = logging.INFO) -> None`, `atlas_engine.observability.get_request_id() -> str | None`, `atlas_engine.observability.RequestIdMiddleware` (a Starlette `BaseHTTPMiddleware` subclass).
- Consumed by: Task 2's exception handler calls `get_request_id()`.

- [ ] **Step 1: Write the failing tests**

Create `engine/tests/test_observability.py`:

```python
import logging

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.observability import _JsonFormatter, get_request_id


def test_request_id_header_is_set_and_unique_per_request():
    client = TestClient(app)
    r1 = client.get("/health")
    r2 = client.get("/health")
    assert r1.headers["x-request-id"]
    assert r2.headers["x-request-id"]
    assert r1.headers["x-request-id"] != r2.headers["x-request-id"]


def test_request_id_header_is_echoed_back_when_client_supplies_one():
    client = TestClient(app)
    response = client.get("/health", headers={"X-Request-Id": "test-fixed-id"})
    assert response.headers["x-request-id"] == "test-fixed-id"


def test_json_formatter_includes_request_id_and_message_fields():
    record = logging.LogRecord("atlas_engine.test", logging.INFO, __file__, 1, "hello", None, None)
    formatted = _JsonFormatter().format(record)
    assert '"message": "hello"' in formatted
    assert '"request_id"' in formatted


def test_get_request_id_is_none_outside_a_request():
    assert get_request_id() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && pytest tests/test_observability.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atlas_engine.observability'`

- [ ] **Step 3: Implement `observability.py`**

Create `engine/src/atlas_engine/observability.py`:

```python
import contextvars
import json
import logging
import sys
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """The current request's id, set by RequestIdMiddleware — None outside
    a request (e.g. a CLI command)."""
    return _request_id_var.get()


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: int = logging.INFO) -> None:
    """Switches the root logger to structured JSON on stdout, for Container
    Apps' log pipeline to pick up with no extra plumbing. Safe to call more
    than once — always ends with exactly one handler."""
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assigns a fresh request id to every inbound request, exposed to
    application code via get_request_id() and echoed back as the
    X-Request-Id response header, so one agent's session can be traced
    end-to-end in Log Analytics."""

    async def dispatch(self, request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        token = _request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            _request_id_var.reset(token)
        response.headers["X-Request-Id"] = request_id
        return response
```

- [ ] **Step 4: Wire it into the app**

Modify `engine/src/atlas_engine/api.py`. Add to the imports (after the existing `from fastapi.middleware.cors import CORSMiddleware` line):

```python
from atlas_engine.observability import RequestIdMiddleware, configure_logging
```

Immediately after `app = FastAPI(title="Atlas Engine Local API")` and before the CORS `app.add_middleware(...)` block, add:

```python
configure_logging()
app.add_middleware(RequestIdMiddleware)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd engine && pytest tests/test_observability.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Run the full test suite to check for regressions**

Run: `cd engine && pytest -v`
Expected: PASS, same count as before this task plus 4

- [ ] **Step 7: Commit**

```bash
git add engine/src/atlas_engine/observability.py engine/src/atlas_engine/api.py engine/tests/test_observability.py
git commit -m "feat: add structured JSON logging and per-request request-id middleware"
```

---

### Task 2: Global exception handler

**Files:**
- Modify: `engine/src/atlas_engine/api.py:1-90` (add `Request` import, `logging` import, module logger, and the handler)
- Test: `engine/tests/test_api_error_handling.py`

**Interfaces:**
- Consumes: `atlas_engine.observability.get_request_id` (Task 1).
- Produces: a registered FastAPI handler for the base `Exception` type — any route's unhandled exception now returns `{"error": "internal_server_error", "request_id": <str|None>}` with status 500, and logs the full traceback server-side. No new importable interface for other tasks.

- [ ] **Step 1: Write the failing test**

Create `engine/tests/test_api_error_handling.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && pytest tests/test_api_error_handling.py -v`
Expected: FAIL — response is a raw 500 with a stack trace in the body (or the request raises inside the test client), not the clean JSON shape.

- [ ] **Step 3: Implement the handler**

In `engine/src/atlas_engine/api.py`, add `Request` to the existing `from fastapi import ...` line so it reads:

```python
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
```

Add near the top, after the existing imports:

```python
import logging

from atlas_engine.observability import RequestIdMiddleware, configure_logging, get_request_id

logger = logging.getLogger(__name__)
```

(This replaces the plain `from atlas_engine.observability import RequestIdMiddleware, configure_logging` added in Task 1 — same line, now importing `get_request_id` too.)

Add, directly after the CORS middleware block:

```python
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    from fastapi.responses import JSONResponse

    logger.exception("Unhandled exception handling %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "request_id": get_request_id()},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd engine && pytest tests/test_api_error_handling.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `cd engine && pytest -v`
Expected: PASS, same count plus 1

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/api.py engine/tests/test_api_error_handling.py
git commit -m "feat: add a global exception handler that logs server-side and returns a clean error to clients"
```

---

### Task 3: Retry-with-backoff for Azure Blob uploads

**Files:**
- Modify: `engine/src/atlas_engine/storage/azure_blob.py` (add `_with_retry`, apply to `AzureRunDir.upload` at line 52-60 and `AzureBlobRunStore.write_bytes` at line 107-109)
- Test: `engine/tests/test_storage_azure_blob.py` (append)

**Interfaces:**
- Produces: `atlas_engine.storage.azure_blob._with_retry(func, *, attempts: int = 3, base_delay: float = 0.5)` — calls `func()` with no args, retrying on any `Exception` with exponential backoff (`base_delay * 2**attempt` seconds between attempts), re-raising the last exception once `attempts` is exhausted.
- Consumed by: Task 4 relies on upload failures still propagating (after retries) so `persist_recording` can catch them.

- [ ] **Step 1: Write the failing tests**

Append to `engine/tests/test_storage_azure_blob.py`:

```python
import pytest

from atlas_engine.storage.azure_blob import _with_retry


def test_with_retry_succeeds_after_transient_failures(monkeypatch):
    monkeypatch.setattr("atlas_engine.storage.azure_blob.time.sleep", lambda _seconds: None)
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] < 3:
            raise ConnectionError("transient")
        return "ok"

    result = _with_retry(flaky)

    assert result == "ok"
    assert calls["count"] == 3


def test_with_retry_raises_after_exhausting_attempts(monkeypatch):
    monkeypatch.setattr("atlas_engine.storage.azure_blob.time.sleep", lambda _seconds: None)

    def always_fails():
        raise ConnectionError("still broken")

    with pytest.raises(ConnectionError, match="still broken"):
        _with_retry(always_fails, attempts=3)


def test_run_dir_upload_goes_through_with_retry(monkeypatch):
    import atlas_engine.storage.azure_blob as azure_blob_module

    store = _store()
    calls = []
    original = azure_blob_module._with_retry

    def spy(func, **kwargs):
        calls.append(1)
        return original(func, **kwargs)

    monkeypatch.setattr(azure_blob_module, "_with_retry", spy)

    run = store.new_run_dir(prefix="goldens")
    (run.path / "golden.py").write_text("print('golden')")
    run.upload("golden.py")

    assert calls == [1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && pytest tests/test_storage_azure_blob.py -v`
Expected: `test_with_retry_*` FAIL with `ImportError: cannot import name '_with_retry'`; `test_run_dir_upload_goes_through_with_retry` errors the same way (or is skipped if Azurite isn't running — start it first: `docker compose -f ../infra/docker-compose.dev.yml up -d azurite`, run from `engine/`).

- [ ] **Step 3: Implement retry and apply it**

In `engine/src/atlas_engine/storage/azure_blob.py`, add `import time` to the top imports (alongside the existing `import json`, `import tempfile`, `import uuid`). Add this function after the imports, before `_account_key`:

```python
def _with_retry(func, *, attempts: int = 3, base_delay: float = 0.5):
    """Retries a transient Azure Blob Storage network call up to `attempts`
    times with exponential backoff (0.5s, 1s, 2s, ...) before giving up —
    'a wrong cached action is worse than a slow one' extends to uploads
    too: a completed-but-slow upload beats a lost recording."""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return func()
        except Exception as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(base_delay * (2 ** attempt))
    raise last_exc
```

Replace the body of `AzureRunDir.upload` (currently lines 52-60):

```python
    def upload(self, name: str) -> str:
        """Uploads a file already written to `self.path / name` (directly,
        or via write_json/write_bytes) to Blob Storage. Returns the full
        blob_ref."""
        blob_ref = f"{self.blob_ref}/{name}"
        blob_client = self._store._container_client.get_blob_client(blob_ref)

        def _do_upload():
            with open(self.path / name, "rb") as fh:
                blob_client.upload_blob(fh, overwrite=True)

        _with_retry(_do_upload)
        return blob_ref
```

Replace the body of `AzureBlobRunStore.write_bytes` (currently lines 107-109):

```python
    def write_bytes(self, blob_ref: str, data: bytes) -> str:
        _with_retry(lambda: self._container_client.get_blob_client(blob_ref).upload_blob(data, overwrite=True))
        return blob_ref
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd engine && pytest tests/test_storage_azure_blob.py -v`
Expected: PASS (all, assuming Azurite is running for the Azurite-backed ones — they `pytest.skip` otherwise, which also counts as passing CI)

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `cd engine && pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/storage/azure_blob.py engine/tests/test_storage_azure_blob.py
git commit -m "feat: retry Azure Blob uploads with exponential backoff on transient failures"
```

---

### Task 4: Recording-upload durability — never silently lose a completed session

**Files:**
- Modify: `engine/src/atlas_engine/recording_service.py:1-139` (restructure `persist_recording`)
- Test: `engine/tests/test_recording_service.py` (append)

**Interfaces:**
- Consumes: nothing new from other tasks (works whether the store is `LocalRunStore` or `AzureBlobRunStore`).
- Produces: no signature change to `persist_recording` — same params, same return (`uuid.UUID`). Behavior change only: the `Recording` row now exists (with `status="capturing"`) before any artifact upload is attempted, and ends as `status="failed"` (not silently absent, not falsely `"captured"`) if any upload step raises after Task 3's retries are exhausted. The original exception still propagates to the caller.

- [ ] **Step 1: Write the failing test**

Append to `engine/tests/test_recording_service.py`:

```python
def test_persist_recording_marks_status_failed_and_reraises_when_an_upload_fails(monkeypatch, fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Upload Failure Org", slug=f"upload-failure-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    actions = [
        RecordedAction(0, "click", [{"selector": "#login-btn", "rank": 0}], None, f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/dashboard.html", "", ""),
    ]

    from atlas_engine.storage.local import RunDir

    def _boom(self, name, data):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(RunDir, "write_json", _boom)

    with pytest.raises(ConnectionError, match="simulated network failure"):
        persist_recording(org_id, f"{fixture_portal_url}/login.html", actions)

    with session_scope() as session:
        recording = session.query(Recording).filter_by(org_id=uuid.UUID(org_id)).one()
        assert recording.status == "failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && pytest tests/test_recording_service.py -v -k upload_fails`
Expected: FAIL — today, `persist_recording` raises before any `Recording` row is ever created, so `session.query(Recording).filter_by(org_id=...).one()` raises `sqlalchemy.exc.NoResultFound`.

- [ ] **Step 3: Restructure `persist_recording`**

Replace the full body of `persist_recording` in `engine/src/atlas_engine/recording_service.py` (currently lines 37-139) with:

```python
def persist_recording(
    org_id: str,
    start_url: str,
    actions: list[RecordedAction],
    *,
    request_id: uuid.UUID | None = None,
    role: str = "demo",
    user_id: str | None = None,
    video_path: Path | None = None,
    video_started_at_ms: int | None = None,
    video_ended_at_ms: int | None = None,
    additional_pages: list[PageCapture] | None = None,
    page_extractions: list[dict] | None = None,
    codegen_script: str | None = None,
    capture_artifacts: list[tuple[str, Path]] | None = None,
) -> uuid.UUID:
    """Persists a captured recording — actions.json, the extracted
    trajectory, one VideoAsset per page (main plus any tabs/popups opened
    during capture), and the Recording/ReferenceTrajectory rows — without
    running any authoring. This is the recording module's one entrypoint;
    authoring_service.author_from_recording is the only thing that
    consumes what this produces.

    The Recording row is created (status="capturing") before any artifact
    upload is attempted, and is updated to status="captured" on success or
    status="failed" on an unrecoverable upload error (after Task 3's
    retries are exhausted) — so a network blip during upload leaves a
    visibly-failed row behind instead of silently discarding a completed
    recording session.
    """
    store = get_store()
    run_dir = store.new_run_dir(prefix="recordings")
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
            status="capturing",
            capture_blob_prefix=run_dir.blob_ref,
            request_id=request_id,
            role=role,
            user_id=uuid.UUID(user_id) if user_id else None,
        )
        session.add(recording)
        session.flush()
        recording_id = recording.id

    try:
        run_dir.write_json("actions.json", [vars(a) for a in actions])

        trajectory = extract_trajectory(actions)
        trajectory_blob_ref = run_dir.write_json("trajectory.json", trajectory_to_dict(trajectory))

        if codegen_script is not None:
            run_dir.write_text("codegen.py", codegen_script)

        for filename, artifact_path in capture_artifacts or []:
            if artifact_path.exists() and artifact_path.is_file() and artifact_path.stat().st_size > 0:
                run_dir.write_bytes(filename, artifact_path.read_bytes())

        with session_scope() as session:
            recording = session.get(Recording, recording_id)

            session.add(
                ReferenceTrajectory(
                    recording_id=recording.id,
                    trajectory_blob_ref=trajectory_blob_ref,
                    step_count=len(trajectory.steps),
                )
            )

            recording.duration_seconds = _persist_video_asset(
                session, org_id, recording.id, run_dir, "video.webm", 0,
                video_path, video_started_at_ms, video_ended_at_ms, actions,
            )

            for page_capture in (additional_pages or []):
                _persist_video_asset(
                    session, org_id, recording.id, run_dir, f"video-page{page_capture.page_index}.webm",
                    page_capture.page_index, page_capture.video_path,
                    page_capture.video_started_at_ms, page_capture.video_ended_at_ms, page_capture.actions,
                )

            for extraction in (page_extractions or []):
                session.add(
                    PageExtraction(
                        recording_id=recording.id,
                        page_index=extraction["page_index"],
                        url=extraction["url"],
                        data=extraction["data"],
                        extracted_at=datetime.now(timezone.utc),
                    )
                )

            if request_id is not None:
                request = session.get(RecordingRequest, request_id)
                if request is not None and request.status == "open":
                    request.status = "submitted"

            recording.status = "captured"
    except Exception:
        logger.exception(
            "Failed to persist recording %s after it was created — marking it failed", recording_id
        )
        with session_scope() as session:
            recording = session.get(Recording, recording_id)
            if recording is not None:
                recording.status = "failed"
        raise

    return recording_id
```

Add the module logger near the top of `engine/src/atlas_engine/recording_service.py`, after the existing imports:

```python
import logging

logger = logging.getLogger(__name__)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd engine && pytest tests/test_recording_service.py -v`
Expected: PASS (all, including the two pre-existing tests in this file)

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `cd engine && pytest -v`
Expected: PASS — this is the highest-risk task in the plan (it restructures a function several other modules call: `live_capture.py`, `cli.py`, the `/author` API route); a full-suite regression run is required, not just this file's tests.

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/recording_service.py engine/tests/test_recording_service.py
git commit -m "fix: keep a recording's DB row alive through upload failure instead of losing it silently"
```

---

### Task 5: CI workflow — tests gate every PR

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:** None (CI config, not application code).

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  engine-tests:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: atlas
          POSTGRES_PASSWORD: atlas_dev_password
          POSTGRES_DB: atlas_test
        ports:
          - 5434:5432
        options: >-
          --health-cmd "pg_isready -U atlas"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 5
    env:
      ATLAS_DATABASE_URL: postgresql+psycopg://atlas:atlas_dev_password@localhost:5434/atlas_test
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install engine
        working-directory: engine
        run: pip install -e ".[dev]"
      - name: Install Playwright Chromium
        working-directory: engine
        run: python -m playwright install --with-deps chromium
      - name: Run migrations
        working-directory: engine
        run: alembic upgrade head
      - name: Run tests
        working-directory: engine
        run: pytest -v

  frontend-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - name: Install dependencies
        working-directory: frontend
        run: npm ci
      - name: Run tests
        working-directory: frontend
        run: npm test
```

- [ ] **Step 2: Validate the YAML and rehearse the engine job locally**

Run:

```bash
python3 -c "import yaml, sys; yaml.safe_load(open('.github/workflows/ci.yml'))" 2>/dev/null || python3 -c "
import json, subprocess
subprocess.run(['python3', '-c', 'import yaml'])
"
```

If `yaml` isn't installed locally, skip the parse check — GitHub will reject invalid YAML on push regardless. Then rehearse the actual commands the job runs, against a disposable test database on the existing dev Postgres:

```bash
docker compose -f infra/docker-compose.dev.yml up -d postgres
docker compose -f infra/docker-compose.dev.yml exec postgres createdb -U atlas atlas_test_ci_rehearsal || true
cd engine
ATLAS_DATABASE_URL="postgresql+psycopg://atlas:atlas_dev_password@localhost:5434/atlas_test_ci_rehearsal" alembic upgrade head
ATLAS_DATABASE_URL="postgresql+psycopg://atlas:atlas_dev_password@localhost:5434/atlas_test_ci_rehearsal" pytest -v
```

Expected: `alembic upgrade head` completes with no errors, `pytest -v` passes with the same count as Task 4's step 5.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run engine pytest and frontend vitest on every PR"
```

---

### Task 6: Dockerfile for `engine/`

**Files:**
- Create: `engine/Dockerfile`
- Create: `engine/docker-entrypoint.sh`
- Create: `engine/.dockerignore`

**Interfaces:**
- Produces: a Docker image that, on start, runs `alembic upgrade head` against `ATLAS_DATABASE_URL` and then serves the FastAPI app via `uvicorn` on port 8000. Consumed by Task 8's `deploy.yml`.

- [ ] **Step 1: Write the entrypoint script**

Create `engine/docker-entrypoint.sh`:

```bash
#!/usr/bin/env sh
set -e
alembic upgrade head
exec uvicorn atlas_engine.api:app --host 0.0.0.0 --port 8000
```

- [ ] **Step 2: Write the Dockerfile**

Create `engine/Dockerfile`. The base image is Playwright's own official Python image, pinned to match `pyproject.toml`'s `playwright>=1.49` floor — it already carries every system dependency Chromium needs, which a plain `python:3.11-slim` base does not:

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY docker-entrypoint.sh ./

RUN pip install --no-cache-dir . && chmod +x docker-entrypoint.sh

ENV ATLAS_RUN_ROOT=/data/atlas-runs
RUN mkdir -p /data/atlas-runs

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
```

- [ ] **Step 3: Write `.dockerignore`**

Create `engine/.dockerignore`:

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
htmlcov/
tests/
.env
*.egg-info/
```

- [ ] **Step 4: Build the image and verify it serves `/health` against a real Postgres**

```bash
docker compose -f infra/docker-compose.dev.yml up -d postgres
docker build -t atlas-engine:test engine/
docker run -d --rm -p 8010:8000 --name atlas-engine-dockertest \
  -e ATLAS_DATABASE_URL="postgresql+psycopg://atlas:atlas_dev_password@host.docker.internal:5434/atlas" \
  -e ATLAS_SESSION_SECRET=test-secret \
  atlas-engine:test
sleep 3
curl -f http://localhost:8010/health
docker stop atlas-engine-dockertest
```

Expected: `curl` prints `{"status":"ok"}` and exits 0. (`host.docker.internal` resolves to the host from inside the container on Docker Desktop for macOS, which this machine runs.)

- [ ] **Step 5: Commit**

```bash
git add engine/Dockerfile engine/docker-entrypoint.sh engine/.dockerignore
git commit -m "build: add a Dockerfile for the engine API, migrating on startup"
```

---

### Task 7: Azure infra provisioning script

**Files:**
- Create: `infra/provision-azure.sh`

**Interfaces:** None (an operational script the user runs against their own Azure subscription — not called by any other task's code).

> **This task provisions real, billed Azure resources.** Run it deliberately, one command at a time if you'd rather review each result, not as unattended CI. `az` CLI flags occasionally drift between versions — if any `create` command below reports an unrecognized argument, run `az <command> create --help` to check the current flag name before changing anything else.

- [ ] **Step 1: Write the provisioning script**

Create `infra/provision-azure.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Provisions the Azure resources the deployed backend needs: a resource
# group, Container Registry, Container Apps environment, PostgreSQL
# Flexible Server, and a storage account. The Container Apps environment
# wires up its own Log Analytics workspace automatically — no separate
# step for that. Re-running this script is safe: each `az ... create`
# no-ops (or errors harmlessly) against a resource that already exists
# with the same name.
#
# Required env vars — no defaults, deliberately, so this never runs
# against a real subscription by accident:
#   AZURE_RESOURCE_GROUP, AZURE_LOCATION, AZURE_ACR_NAME,
#   AZURE_CONTAINERAPPS_ENV, AZURE_POSTGRES_SERVER_NAME,
#   AZURE_POSTGRES_ADMIN_PASSWORD, AZURE_STORAGE_ACCOUNT_NAME

: "${AZURE_RESOURCE_GROUP:?set AZURE_RESOURCE_GROUP}"
: "${AZURE_LOCATION:?set AZURE_LOCATION}"
: "${AZURE_ACR_NAME:?set AZURE_ACR_NAME}"
: "${AZURE_CONTAINERAPPS_ENV:?set AZURE_CONTAINERAPPS_ENV}"
: "${AZURE_POSTGRES_SERVER_NAME:?set AZURE_POSTGRES_SERVER_NAME}"
: "${AZURE_POSTGRES_ADMIN_PASSWORD:?set AZURE_POSTGRES_ADMIN_PASSWORD}"
: "${AZURE_STORAGE_ACCOUNT_NAME:?set AZURE_STORAGE_ACCOUNT_NAME}"

echo "Creating resource group $AZURE_RESOURCE_GROUP in $AZURE_LOCATION..."
az group create --name "$AZURE_RESOURCE_GROUP" --location "$AZURE_LOCATION"

echo "Creating Container Registry $AZURE_ACR_NAME..."
az acr create --resource-group "$AZURE_RESOURCE_GROUP" --name "$AZURE_ACR_NAME" \
  --sku Basic --admin-enabled true

echo "Creating PostgreSQL Flexible Server $AZURE_POSTGRES_SERVER_NAME..."
az postgres flexible-server create \
  --resource-group "$AZURE_RESOURCE_GROUP" --name "$AZURE_POSTGRES_SERVER_NAME" \
  --location "$AZURE_LOCATION" --admin-user atlas --admin-password "$AZURE_POSTGRES_ADMIN_PASSWORD" \
  --sku-name Standard_B1ms --tier Burstable --storage-size 32 --version 16 \
  --database-name atlas --public-access 0.0.0.0

echo "Creating storage account $AZURE_STORAGE_ACCOUNT_NAME..."
az storage account create \
  --resource-group "$AZURE_RESOURCE_GROUP" --name "$AZURE_STORAGE_ACCOUNT_NAME" \
  --location "$AZURE_LOCATION" --sku Standard_LRS

echo "Creating Container Apps environment $AZURE_CONTAINERAPPS_ENV..."
az containerapp env create \
  --resource-group "$AZURE_RESOURCE_GROUP" --name "$AZURE_CONTAINERAPPS_ENV" \
  --location "$AZURE_LOCATION"

echo "Done. Next: Task 8 creates the Container App itself on first deploy."
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x infra/provision-azure.sh
```

- [ ] **Step 3: Run it against your Azure subscription and verify each resource**

```bash
export AZURE_RESOURCE_GROUP=atlas-ai-prod
export AZURE_LOCATION=eastus
export AZURE_ACR_NAME=atlasairegistry     # must be globally unique
export AZURE_CONTAINERAPPS_ENV=atlas-ai-env
export AZURE_POSTGRES_SERVER_NAME=atlas-ai-postgres
export AZURE_POSTGRES_ADMIN_PASSWORD='<pick a real secret, not this placeholder>'
export AZURE_STORAGE_ACCOUNT_NAME=atlasaistorage   # must be globally unique, lowercase, no dashes

az login
./infra/provision-azure.sh

az group show --name "$AZURE_RESOURCE_GROUP"
az acr show --name "$AZURE_ACR_NAME" --resource-group "$AZURE_RESOURCE_GROUP"
az postgres flexible-server show --name "$AZURE_POSTGRES_SERVER_NAME" --resource-group "$AZURE_RESOURCE_GROUP"
az storage account show --name "$AZURE_STORAGE_ACCOUNT_NAME" --resource-group "$AZURE_RESOURCE_GROUP"
az containerapp env show --name "$AZURE_CONTAINERAPPS_ENV" --resource-group "$AZURE_RESOURCE_GROUP"
```

Expected: each `show` command prints that resource's JSON description (not a "not found" error).

- [ ] **Step 4: Commit**

```bash
git add infra/provision-azure.sh
git commit -m "infra: add Azure provisioning script for the production backend"
```

---

### Task 8: Deploy workflow — build, push, deploy on merge to `main`

**Files:**
- Create: `.github/workflows/deploy.yml`

**Interfaces:** None (CI/CD config). Depends on Task 6's `Dockerfile` and Task 7's provisioned resources existing.

> **One-time manual setup, before this workflow can run** (do this in the Azure Portal / `az` CLI and the GitHub repo settings — not something to automate here, since it grants a real credential):
> 1. Create a Microsoft Entra app registration with a federated credential trusting this GitHub repo's `main` branch (Azure Portal → *App registrations* → *New registration*, then *Certificates & secrets* → *Federated credentials* → *GitHub Actions deploying Azure resources*). Note its **Application (client) ID**.
> 2. Grant that app `Contributor` on the resource group created in Task 7: `az role assignment create --assignee <client-id> --role Contributor --scope /subscriptions/<sub-id>/resourceGroups/<AZURE_RESOURCE_GROUP>`.
> 3. In the GitHub repo settings, add an environment named `production` (Settings → Environments → New environment).
> 4. Add repo **secrets** (Settings → Secrets and variables → Actions → Secrets): `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`.
> 5. Add repo **variables** (same page, *Variables* tab): `AZURE_ACR_NAME`, `AZURE_RESOURCE_GROUP`, `AZURE_CONTAINERAPP_NAME` (pick a name for the Container App itself, e.g. `atlas-engine-api` — this workflow creates it on its first run if it doesn't already exist).

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy

on:
  push:
    branches: [main]
    paths:
      - "engine/**"
      - ".github/workflows/deploy.yml"

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - uses: actions/checkout@v4

      - name: Log in to Azure
        uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}

      - name: Log in to ACR
        run: az acr login --name ${{ vars.AZURE_ACR_NAME }}

      - name: Build and push image
        working-directory: engine
        run: |
          IMAGE="${{ vars.AZURE_ACR_NAME }}.azurecr.io/atlas-engine-api:${{ github.sha }}"
          docker build -t "$IMAGE" .
          docker push "$IMAGE"
          echo "IMAGE=$IMAGE" >> "$GITHUB_ENV"

      - name: Deploy to Container Apps
        run: |
          if az containerapp show --name "${{ vars.AZURE_CONTAINERAPP_NAME }}" --resource-group "${{ vars.AZURE_RESOURCE_GROUP }}" >/dev/null 2>&1; then
            az containerapp update \
              --name "${{ vars.AZURE_CONTAINERAPP_NAME }}" \
              --resource-group "${{ vars.AZURE_RESOURCE_GROUP }}" \
              --image "$IMAGE"
          else
            az containerapp create \
              --name "${{ vars.AZURE_CONTAINERAPP_NAME }}" \
              --resource-group "${{ vars.AZURE_RESOURCE_GROUP }}" \
              --environment "${{ vars.AZURE_CONTAINERAPPS_ENV }}" \
              --image "$IMAGE" \
              --target-port 8000 \
              --ingress external \
              --min-replicas 1 \
              --registry-server "${{ vars.AZURE_ACR_NAME }}.azurecr.io" \
              --secrets \
                atlas-database-url="${{ secrets.ATLAS_DATABASE_URL }}" \
                atlas-session-secret="${{ secrets.ATLAS_SESSION_SECRET }}" \
                atlas-storage-conn="${{ secrets.ATLAS_AZURE_STORAGE_CONNECTION_STRING }}" \
              --env-vars \
                ATLAS_DATABASE_URL=secretref:atlas-database-url \
                ATLAS_SESSION_SECRET=secretref:atlas-session-secret \
                ATLAS_AZURE_STORAGE_CONNECTION_STRING=secretref:atlas-storage-conn \
                ATLAS_REQUIRE_AZURE_STORAGE=true
          fi
```

`AZURE_CONTAINERAPPS_ENV` needs to be a repo **variable** too (add it alongside the others from the setup note above) — it's only read on the `containerapp create` branch, the very first deploy. `ATLAS_DATABASE_URL`, `ATLAS_SESSION_SECRET`, and `ATLAS_AZURE_STORAGE_CONNECTION_STRING` need to be added as repo **secrets** before that first deploy (`ATLAS_DATABASE_URL` should point at Task 7's Flexible Server; generate a real random value for `ATLAS_SESSION_SECRET`, e.g. `openssl rand -hex 32`).

- [ ] **Step 2: Validate the YAML locally**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml'))"
```

Expected: no output, exit code 0. (Install with `pip install pyyaml` first if it's not already available.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: deploy the engine API to Azure Container Apps on merge to main"
```

- [ ] **Step 4: First real deploy and smoke test**

Complete the one-time manual setup above, add the required secrets/variables, then merge this branch to `main`. Watch the `Deploy` workflow run in the GitHub Actions tab. Once it succeeds:

```bash
CONTAINERAPP_URL=$(az containerapp show --name <AZURE_CONTAINERAPP_NAME> --resource-group <AZURE_RESOURCE_GROUP> --query properties.configuration.ingress.fqdn -o tsv)
curl -f "https://$CONTAINERAPP_URL/health"
```

Expected: `{"status":"ok"}`. Do not point any real agent's exe at this URL until this smoke test passes.

---

## Plan Self-Review Notes

- **Spec coverage:** §5 (config/secrets) → Task 8's `--secrets`/`--env-vars`; §6 (logging) → Task 1; §7 (error handling) → Tasks 2, 3, 4; §8 (CI/CD) → Tasks 5, 6, 8; §9 (testing) → every task's own test; §10 (rollout) → Tasks 7 (steps 1) and 6/8 (steps 2-5). No spec section is without a task.
- **Status enum:** confirmed `RECORDING_STATUSES` already includes `"failed"` in the current schema (`engine/src/atlas_engine/db/models/portal.py:11`) — Task 4 needs no migration.
- **Type/name consistency checked:** `get_store()`, `RunDir`/`AzureRunDir`, `session_scope()`, `Recording.status` values, and `persist_recording`'s exact parameter list are used identically across Tasks 3, 4, and their tests, matching the current source read during planning.
