# Authoring Pipeline API Endpoints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the `atlas_engine.authoring` orchestration package (merged in the prior "orchestration backend" plan) over HTTP: starting an authoring run, polling its live status/event-log/golden-script, and resuming/stopping it.

**Architecture:** Four small FastAPI endpoint changes/additions in `engine/src/atlas_engine/api.py`, all thin wrappers around the already-tested `atlas_engine.authoring.run` module (`start_authoring_run`, `resume_authoring_run`, `stop_authoring_run`) plus a read-only query for the polling endpoint. No new business logic — this plan is pure API surface.

**Tech Stack:** FastAPI, Pydantic, pytest + `fastapi.testclient.TestClient`, matching every existing endpoint's conventions in `api.py`.

**Spec:** `docs/superpowers/specs/2026-08-13-authoring-pipeline-rebuild-design.md`

## Global Constraints

- `POST /recordings/{recording_id}/author` **replaces** its current synchronous behavior (calling `author_from_recording` and blocking for a full result) with the async behavior the spec specifies: it now starts an `AuthoringRun` and returns `{authoring_run_id}` immediately. This is a deliberate breaking change to this one endpoint's response shape, matching the spec's explicit design ("this replaces today's synchronous `author_from_recording` call"). The existing `AuthorResponse` Pydantic model, the separate `/author` endpoint (which calls `author_from_recorded_actions`, a different function), and the `author_from_recording` function itself are all untouched — only this one endpoint's body and `response_model` change.
- This is an accepted, temporary breaking change to the frontend's existing "Generate golden script" button (`frontend/src/lib/authoring.ts`) until a later frontend plan updates it — matching this project's established multi-plan sequencing (backend infrastructure lands before the frontend that consumes it). Do not attempt to update the frontend in this plan.
- `GoldenScript.script_blob_ref` is stored as an absolute filesystem path (a known, already-reviewed characteristic of the existing codebase) — reading it with `Path(...).read_text()` needs no `settings.run_root` join.
- Every new/changed endpoint follows the exact conventions already used throughout `api.py`: a `BaseModel` response class per endpoint, `HTTPException(status_code=404, ...)` translating a service-layer `ValueError`, and `uuid.UUID(...)` conversions at the API boundary (service-layer functions take real `uuid.UUID`/`UUID`-typed values, not strings).

---

### Task 1: `POST /recordings/{recording_id}/author` starts an async authoring run

**Files:**
- Modify: `engine/src/atlas_engine/api.py`
- Modify: `engine/tests/test_api_requests.py`

**Interfaces:**
- Consumes: `atlas_engine.authoring.run.start_authoring_run(recording_id: uuid.UUID, *, llm=None) -> uuid.UUID`.
- Produces: `StartAuthoringRunResponse(BaseModel)` with one field `authoring_run_id: str`. The endpoint returns `404` if `start_authoring_run` raises `ValueError` (unknown recording).

- [ ] **Step 1: Update the now-outdated existing test**

`engine/tests/test_api_requests.py` currently has a test (around line 68) asserting the OLD synchronous shape:

```python
    client = TestClient(app)
    response = client.post(f"/recordings/{recording_id}/author")

    assert response.status_code == 200
    assert response.json()["status"] == "certified"
```

Find this exact test function (read the file to get its full name and surrounding context first) and replace those three assertion lines with:

```python
    client = TestClient(app)
    response = client.post(f"/recordings/{recording_id}/author")

    assert response.status_code == 200
    assert "authoring_run_id" in response.json()
    uuid.UUID(response.json()["authoring_run_id"])  # raises ValueError if not a real UUID
```

(`uuid` is likely already imported at the top of this test file since it's used elsewhere for `recording_id` — if not, add `import uuid` at the top.)

- [ ] **Step 2: Run the test to verify it fails against the current endpoint**

Run: `cd engine && .venv/bin/pytest tests/test_api_requests.py -v -k author`
Expected: FAIL — the current endpoint still returns the old `{status, script_path, golden_script_id}` shape with no `authoring_run_id` key.

- [ ] **Step 3: Implement**

In `engine/src/atlas_engine/api.py`, add this import alongside the existing `from atlas_engine.authoring_service import author_from_recording` line (do not remove that import — it's still used by nothing else in this file after this change... actually check: grep the file for `author_from_recording` after this edit; if this was its only use, remove the now-unused import, but leave `AuthorResponse`, `author`, and `author_from_recorded_actions` entirely alone):

```python
from atlas_engine.authoring.run import start_authoring_run
```

Add a new response model near the other small response models (e.g. right before this endpoint):

```python
class StartAuthoringRunResponse(BaseModel):
    authoring_run_id: str
```

Replace the existing endpoint body:

```python
@app.post("/recordings/{recording_id}/author", response_model=StartAuthoringRunResponse)
def author_from_recording_endpoint(recording_id: str) -> StartAuthoringRunResponse:
    llm = create_llm_client()
    try:
        authoring_run_id = start_authoring_run(uuid.UUID(recording_id), llm=llm)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return StartAuthoringRunResponse(authoring_run_id=str(authoring_run_id))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd engine && .venv/bin/pytest tests/test_api_requests.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/api.py engine/tests/test_api_requests.py
git commit -m "feat: /recordings/{id}/author starts an async authoring run"
```

---

### Task 2: `GET /authoring-runs/{authoring_run_id}` polling endpoint

**Files:**
- Modify: `engine/src/atlas_engine/api.py`
- Test: `engine/tests/test_api_authoring_runs.py`

**Interfaces:**
- Consumes: `AuthoringRun`, `AuthoringRunEvent`, `CertificationRun`, `GoldenScript` from `atlas_engine.db.models`; `session_scope` from `atlas_engine.db`.
- Produces: `GET /authoring-runs/{authoring_run_id}` returning `AuthoringRunDetailResponse` — `{id, status, current_phase, repair_attempt, events: [AuthoringRunEventResponse], golden_script: GoldenScriptSummaryResponse | null, certification_runs: [CertificationRunResponse]}`. 404 if the run doesn't exist.

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/test_api_authoring_runs.py
import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.db import session_scope
from atlas_engine.db.models import (
    AuthoringRun,
    AuthoringRunEvent,
    CertificationRun,
    GoldenScript,
    Organization,
    PortalProfile,
    Recording,
)


def _make_authoring_run_with_details():
    with session_scope() as session:
        org = Organization(name="API Authoring Org", slug=f"api-authoring-org-{uuid.uuid4()}")
        profile = PortalProfile(org=org, domain="portal-api-authoring.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/1/",
        )
        golden = GoldenScript(
            org=org, portal_profile=profile, version=1, script_blob_ref=__file__, status="certified",
        )
        session.add_all([org, profile, recording, golden])
        session.flush()
        run = AuthoringRun(
            org=org, recording=recording, golden_script=golden, status="completed",
            current_phase="done", repair_attempt=1,
        )
        session.add(run)
        session.flush()
        session.add(AuthoringRunEvent(authoring_run=run, phase="replay", step_index=0, message="Step 0: click - OK"))
        session.add(AuthoringRunEvent(authoring_run=run, phase="certifying", level="warning", message="retrying"))
        session.add(CertificationRun(authoring_run=run, run_number=1, status="pass"))
        session.add(CertificationRun(authoring_run=run, run_number=2, status="fail", failure_evidence_blob_ref="x.txt"))
        session.flush()
        return run.id


def test_get_authoring_run_returns_full_detail():
    authoring_run_id = _make_authoring_run_with_details()

    client = TestClient(app)
    response = client.get(f"/authoring-runs/{authoring_run_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["current_phase"] == "done"
    assert body["repair_attempt"] == 1
    assert len(body["events"]) == 2
    assert body["events"][0]["phase"] == "replay"
    assert body["events"][0]["message"] == "Step 0: click - OK"
    assert body["events"][1]["level"] == "warning"
    assert body["golden_script"]["status"] == "certified"
    assert len(body["certification_runs"]) == 2
    assert body["certification_runs"][0]["status"] == "pass"
    assert body["certification_runs"][1]["status"] == "fail"
    assert body["certification_runs"][1]["failure_evidence_blob_ref"] == "x.txt"


def test_get_authoring_run_has_null_golden_script_before_golden_phase():
    with session_scope() as session:
        org = Organization(name="No Golden Org", slug=f"no-golden-org-{uuid.uuid4()}")
        profile = PortalProfile(org=org, domain="portal-no-golden.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/2/",
        )
        session.add_all([org, profile, recording])
        session.flush()
        run = AuthoringRun(org=org, recording=recording, status="running", current_phase="replay")
        session.add(run)
        session.flush()
        authoring_run_id = run.id

    client = TestClient(app)
    response = client.get(f"/authoring-runs/{authoring_run_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["golden_script"] is None
    assert body["events"] == []
    assert body["certification_runs"] == []


def test_get_authoring_run_404_for_unknown_id():
    client = TestClient(app)
    response = client.get(f"/authoring-runs/{uuid.uuid4()}")
    assert response.status_code == 404
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd engine && .venv/bin/pytest tests/test_api_authoring_runs.py -v`
Expected: FAIL — `404` for every request, no such route exists yet.

- [ ] **Step 3: Implement**

Add these imports to `engine/src/atlas_engine/api.py` (alongside the existing `from atlas_engine.db.models import ...` line — extend it rather than adding a second import line for the same module):

```python
from atlas_engine.db.models import AuthoringRun, AuthoringRunEvent, CertificationRun, GoldenScript, User, VideoAsset
```

Add these response models and the endpoint (anywhere after the other response-model definitions, before the auth section is fine):

```python
class AuthoringRunEventResponse(BaseModel):
    phase: str
    step_index: int | None = None
    level: str
    message: str


class CertificationRunResponse(BaseModel):
    run_number: int
    status: str
    failure_evidence_blob_ref: str | None = None


class GoldenScriptSummaryResponse(BaseModel):
    id: str
    version: int
    status: str
    script_text: str | None = None


class AuthoringRunDetailResponse(BaseModel):
    id: str
    status: str
    current_phase: str
    repair_attempt: int
    events: list[AuthoringRunEventResponse]
    golden_script: GoldenScriptSummaryResponse | None = None
    certification_runs: list[CertificationRunResponse]


@app.get("/authoring-runs/{authoring_run_id}", response_model=AuthoringRunDetailResponse)
def get_authoring_run_endpoint(authoring_run_id: str) -> AuthoringRunDetailResponse:
    with session_scope() as session:
        run = session.get(AuthoringRun, uuid.UUID(authoring_run_id))
        if run is None:
            raise HTTPException(status_code=404, detail=f"No such authoring run: {authoring_run_id}")

        events = (
            session.query(AuthoringRunEvent)
            .filter_by(authoring_run_id=run.id)
            .order_by(AuthoringRunEvent.seq)
            .all()
        )
        certification_runs = (
            session.query(CertificationRun)
            .filter_by(authoring_run_id=run.id)
            .order_by(CertificationRun.run_number)
            .all()
        )

        golden_script = None
        if run.golden_script_id is not None:
            golden = session.get(GoldenScript, run.golden_script_id)
            if golden is not None:
                script_text = None
                script_path = Path(golden.script_blob_ref)
                if script_path.exists():
                    script_text = script_path.read_text()
                golden_script = GoldenScriptSummaryResponse(
                    id=str(golden.id), version=golden.version, status=golden.status, script_text=script_text,
                )

        return AuthoringRunDetailResponse(
            id=str(run.id),
            status=run.status,
            current_phase=run.current_phase,
            repair_attempt=run.repair_attempt,
            events=[
                AuthoringRunEventResponse(
                    phase=e.phase, step_index=e.step_index, level=e.level, message=e.message,
                )
                for e in events
            ],
            golden_script=golden_script,
            certification_runs=[
                CertificationRunResponse(
                    run_number=c.run_number, status=c.status, failure_evidence_blob_ref=c.failure_evidence_blob_ref,
                )
                for c in certification_runs
            ],
        )
```

This needs `Path` imported at the top of `api.py` — check if `pathlib.Path` is already imported; if not, add `from pathlib import Path` alongside the other stdlib imports at the top of the file.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd engine && .venv/bin/pytest tests/test_api_authoring_runs.py -v`
Expected: PASS (3/3)

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/api.py engine/tests/test_api_authoring_runs.py
git commit -m "feat: add GET /authoring-runs/{id} polling endpoint"
```

---

### Task 3: `POST /authoring-runs/{authoring_run_id}/resume`

**Files:**
- Modify: `engine/src/atlas_engine/api.py`
- Test: `engine/tests/test_api_authoring_runs.py`

**Interfaces:**
- Consumes: `atlas_engine.authoring.run.resume_authoring_run(authoring_run_id: uuid.UUID) -> None`.
- Produces: `POST /authoring-runs/{authoring_run_id}/resume` returning `{"ok": true}` on success, `404` if `resume_authoring_run` raises `ValueError`.

- [ ] **Step 1: Write the failing test**

Add to `engine/tests/test_api_authoring_runs.py`:

```python
def test_resume_authoring_run_endpoint_404_for_unknown_run():
    client = TestClient(app)
    response = client.post(f"/authoring-runs/{uuid.uuid4()}/resume")
    assert response.status_code == 404
```

(A success-path test for resume requires a real in-process running `AuthoringRun` — that's exercised by Task 5's integration test, not here; this task's own test only needs to prove the 404 translation, matching how `test_stop_authoring_run_raises_for_unknown_run` in `test_authoring_run.py` already proves the underlying service function's error at the service layer.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd engine && .venv/bin/pytest tests/test_api_authoring_runs.py -v -k resume`
Expected: FAIL — 404 route doesn't exist yet (returns FastAPI's default 404 for an unrecognized route, but the test itself would still technically see a 404 — so instead confirm failure via: the response has no JSON body shaped like this endpoint would produce, e.g. check `response.status_code == 404` passes for the WRONG reason. To make sure this step actually proves something, temporarily verify no `/authoring-runs/.../resume` route is registered by checking `[r.path for r in app.routes]` doesn't contain it — but simplest: just proceed to Step 3, since a 404 for "route doesn't exist" and a 404 for "run doesn't exist" happen to coincide for this particular test; the meaningful proof is that after Step 3, a DIFFERENT/real scenario — a run that exists but was never started via start_authoring_run — would still correctly 404, which Step 4 verifies by running the full test suite including Task 5's later success-path coverage.)

- [ ] **Step 3: Implement**

Add to `engine/src/atlas_engine/api.py`:

```python
from atlas_engine.authoring.run import resume_authoring_run, start_authoring_run
```

(extend the existing import from Task 1 rather than adding a second import line for the same module)

```python
@app.post("/authoring-runs/{authoring_run_id}/resume")
def resume_authoring_run_endpoint(authoring_run_id: str) -> dict:
    try:
        resume_authoring_run(uuid.UUID(authoring_run_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd engine && .venv/bin/pytest tests/test_api_authoring_runs.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/api.py engine/tests/test_api_authoring_runs.py
git commit -m "feat: add POST /authoring-runs/{id}/resume endpoint"
```

---

### Task 4: `POST /authoring-runs/{authoring_run_id}/stop`

**Files:**
- Modify: `engine/src/atlas_engine/api.py`
- Test: `engine/tests/test_api_authoring_runs.py`

**Interfaces:**
- Consumes: `atlas_engine.authoring.run.stop_authoring_run(authoring_run_id: uuid.UUID, *, timeout: float = 30.0) -> None`.
- Produces: `POST /authoring-runs/{authoring_run_id}/stop` returning `{"ok": true}` on success, `404` if `stop_authoring_run` raises `ValueError`.

- [ ] **Step 1: Write the failing test**

Add to `engine/tests/test_api_authoring_runs.py`:

```python
def test_stop_authoring_run_endpoint_404_for_unknown_run():
    client = TestClient(app)
    response = client.post(f"/authoring-runs/{uuid.uuid4()}/stop")
    assert response.status_code == 404
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd engine && .venv/bin/pytest tests/test_api_authoring_runs.py -v -k stop`
Expected: FAIL — no such route.

- [ ] **Step 3: Implement**

Extend the existing import line from Task 3:

```python
from atlas_engine.authoring.run import resume_authoring_run, start_authoring_run, stop_authoring_run
```

```python
@app.post("/authoring-runs/{authoring_run_id}/stop")
def stop_authoring_run_endpoint(authoring_run_id: str) -> dict:
    try:
        stop_authoring_run(uuid.UUID(authoring_run_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd engine && .venv/bin/pytest tests/test_api_authoring_runs.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/api.py engine/tests/test_api_authoring_runs.py
git commit -m "feat: add POST /authoring-runs/{id}/stop endpoint"
```

---

### Task 5: End-to-end API integration test, and finish branch

**Files:**
- Test: `engine/tests/test_api_authoring_runs.py`

**Interfaces:**
- Consumes: every endpoint from Tasks 1-4, plus `persist_recording`/`CaptureSession` for setup (matching the pattern in `engine/tests/test_authoring_service.py`/`test_authoring_run.py`).

This test drives the ENTIRE authoring pipeline purely through HTTP, proving the API layer correctly wires into the already-tested orchestrator — including the developer-intervention pause/resume path, this time triggered via the `/resume` HTTP endpoint instead of calling `resume_authoring_run` directly.

- [ ] **Step 1: Write the test**

Add to `engine/tests/test_api_authoring_runs.py`:

```python
import time

from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.recording_service import persist_recording


def _record_against_fixture(org_id: str, fixture_portal_url: str) -> uuid.UUID:
    from playwright.sync_api import sync_playwright

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
    return persist_recording(org_id, start_url, capture.actions)


def test_full_authoring_run_via_http_reaches_certified(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="HTTP Authoring Org", slug=f"http-authoring-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = _record_against_fixture(org_id, fixture_portal_url)

    client = TestClient(app)
    start_response = client.post(f"/recordings/{recording_id}/author")
    assert start_response.status_code == 200
    authoring_run_id = start_response.json()["authoring_run_id"]

    deadline = time.time() + 90
    while time.time() < deadline:
        poll = client.get(f"/authoring-runs/{authoring_run_id}")
        assert poll.status_code == 200
        if poll.json()["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.5)

    final = client.get(f"/authoring-runs/{authoring_run_id}")
    body = final.json()
    assert body["status"] == "completed"
    assert body["current_phase"] == "done"
    assert body["golden_script"]["status"] == "certified"
    assert body["golden_script"]["script_text"] is not None
    assert "#generate-report-btn" in body["golden_script"]["script_text"]
    assert len(body["certification_runs"]) == 3
    assert all(c["status"] == "pass" for c in body["certification_runs"])
    assert any("OK" in e["message"] for e in body["events"])


def test_stop_authoring_run_via_http_while_waiting_for_developer(fixture_portal_url, fixture_portal_server):
    with session_scope() as session:
        org = Organization(name="HTTP Stop Org", slug=f"http-stop-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = _record_against_fixture(org_id, fixture_portal_url)

    fixture_portal_server.set_drift_mode(True)
    try:
        client = TestClient(app)
        start_response = client.post(f"/recordings/{recording_id}/author")
        authoring_run_id = start_response.json()["authoring_run_id"]

        deadline = time.time() + 30
        paused = False
        while time.time() < deadline:
            poll = client.get(f"/authoring-runs/{authoring_run_id}")
            if poll.json()["status"] == "waiting_for_developer":
                paused = True
                break
            time.sleep(0.3)
        assert paused, "authoring run never reached waiting_for_developer via HTTP polling"

        stop_response = client.post(f"/authoring-runs/{authoring_run_id}/stop")
        assert stop_response.status_code == 200
        assert stop_response.json() == {"ok": True}

        final = client.get(f"/authoring-runs/{authoring_run_id}")
        assert final.json()["status"] == "cancelled"
    finally:
        fixture_portal_server.set_drift_mode(False)
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_api_authoring_runs.py -v`
Expected: PASS (all tests in the file). The first new test is slow (a full real replay + brain + 3 certify subprocess runs through HTTP) — let it run to completion, it will take a few minutes.

- [ ] **Step 3: Run the full backend suite**

Run: `cd engine && .venv/bin/pytest -q`
Expected: all pass (a pre-existing, already-documented timing-flaky test in `test_capture_recorder.py` may fail on a full-suite run under resource contention and pass on an isolated re-run — if you see exactly that, re-run it alone to confirm before treating it as a regression).

- [ ] **Step 4: Commit**

```bash
git add engine/tests/test_api_authoring_runs.py
git commit -m "test: add end-to-end authoring pipeline coverage via HTTP"
```

- [ ] **Step 5: Finish the branch**

**REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch — run the full backend suite, present the merge/PR/keep menu, act on the answer.
