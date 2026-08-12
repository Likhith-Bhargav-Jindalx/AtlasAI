# Frontend Backend Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three remaining backend gaps that block the real frontend from working at all: the API doesn't allow cross-origin cookie auth from the Vite dev server, and there's no way to list recordings or heal events (only single-item detail/action endpoints exist today).

**Architecture:** Additive only — a CORS middleware registration, and two new `GET` list endpoints following the exact pattern `list_recording_requests`/`GET /recording-requests` already established (a `list_*` service function returning `list[dict]`, wrapped by a thin endpoint).

**Tech Stack:** FastAPI `CORSMiddleware`; no schema changes.

## Global Constraints

- No new tables or columns — every field these endpoints return already exists.
- List endpoints are org-scoped (`org_id` required query param) and return newest-first, matching `list_recording_requests`'s existing convention.

---

### Task 1: CORS for the Vite dev server

**Files:**
- Modify: `engine/src/atlas_engine/api.py`
- Test: `engine/tests/test_api_cors.py`

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/test_api_cors.py
from fastapi.testclient import TestClient

from atlas_engine.api import app


def test_cors_allows_credentialed_requests_from_the_vite_dev_server():
    client = TestClient(app)
    response = client.get("/health", headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_api_cors.py -v`
Expected: FAIL — `KeyError: 'access-control-allow-origin'` (no CORS headers yet).

- [ ] **Step 3: Implement**

In `engine/src/atlas_engine/api.py`, add the import and register the middleware right after `app = FastAPI(...)`:

```python
from fastapi.middleware.cors import CORSMiddleware
```

```python
app = FastAPI(title="Atlas Engine Local API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_api_cors.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/atlas_engine/api.py tests/test_api_cors.py
git commit -m "feat: allow credentialed CORS requests from the Vite dev server"
```

---

### Task 2: `GET /recordings` — list endpoint

**Files:**
- Modify: `engine/src/atlas_engine/recording_service.py`
- Modify: `engine/src/atlas_engine/api.py`
- Test: `engine/tests/test_api_recordings.py`

**Interfaces:**
- Produces: `list_recordings(org_id: str, status: str | None = None) -> list[dict]` with keys `id, status, role, request_id, request_title, portal_domain, duration_seconds, has_video, created_at`. `GET /recordings?org_id=...&status=...` returns this as JSON.

- [ ] **Step 1: Write the failing test**

Add to `engine/tests/test_api_recordings.py`:

```python
def test_list_recordings_returns_newest_first_and_filters_by_status(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="List Recordings Org", slug=f"list-recordings-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    older_id = persist_recording(org_id, start_url, [])
    newer_id = persist_recording(org_id, start_url, [])

    with session_scope() as session:
        from atlas_engine.db.models import Recording
        session.get(Recording, older_id).status = "discarded"

    client = TestClient(app)
    response = client.get(f"/recordings?org_id={org_id}")

    assert response.status_code == 200
    ids = [r["id"] for r in response.json()]
    assert ids[0] == str(newer_id)
    assert str(older_id) in ids

    filtered = client.get(f"/recordings?org_id={org_id}&status=discarded")
    filtered_ids = [r["id"] for r in filtered.json()]
    assert filtered_ids == [str(older_id)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_api_recordings.py -v`
Expected: FAIL — no matching route for `GET /recordings`.

- [ ] **Step 3: Implement `list_recordings`**

In `engine/src/atlas_engine/recording_service.py`, extend the existing `db.models` import to add `PortalProfile` (it currently imports `Recording, RecordingRequest, ReferenceTrajectory, VideoAsset, VideoStep`). Then add:

```python
def list_recordings(org_id: str, status: str | None = None) -> list[dict]:
    with session_scope() as session:
        query = session.query(Recording).filter_by(org_id=uuid.UUID(org_id))
        if status is not None:
            query = query.filter_by(status=status)

        results = []
        for recording in query.order_by(Recording.created_at.desc()).all():
            video_asset = session.query(VideoAsset).filter_by(recording_id=recording.id).one_or_none()
            request = session.get(RecordingRequest, recording.request_id) if recording.request_id else None
            profile = session.get(PortalProfile, recording.portal_profile_id)
            results.append({
                "id": str(recording.id),
                "status": recording.status,
                "role": recording.role,
                "request_id": str(recording.request_id) if recording.request_id else None,
                "request_title": request.title if request is not None else None,
                "portal_domain": profile.domain if profile is not None else None,
                "duration_seconds": recording.duration_seconds,
                "has_video": video_asset is not None,
                "created_at": recording.created_at.isoformat(),
            })
        return results
```

- [ ] **Step 4: Implement the endpoint**

In `engine/src/atlas_engine/api.py`, extend the recording_service import to add `list_recordings`, then add:

```python
@app.get("/recordings")
def list_recordings_endpoint(org_id: str, status: str | None = None) -> list[dict]:
    return list_recordings(org_id, status=status)
```

Place it near the other `/recordings` routes for readability — `/recordings` (no trailing segment) and `/recordings/{recording_id}` are structurally distinct patterns, so registration order doesn't affect matching here.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_api_recordings.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/atlas_engine/recording_service.py src/atlas_engine/api.py tests/test_api_recordings.py
git commit -m "feat: add GET /recordings list endpoint"
```

---

### Task 3: `GET /heal-events` — list endpoint

**Files:**
- Create: `engine/tests/test_api_heal_list.py`
- Modify: `engine/src/atlas_engine/verify/heal.py`
- Modify: `engine/src/atlas_engine/api.py`

**Interfaces:**
- Produces: `list_heal_events(org_id: str, status: str | None = None) -> list[dict]` with keys `id, golden_script_id, trigger, status, diff_summary, created_at`. `GET /heal-events?org_id=...&status=...` returns this as JSON.

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/test_api_heal_list.py
import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, HealEvent, Organization
from atlas_engine.db.repo import get_or_create_portal_profile


def test_list_heal_events_filters_by_status(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Heal List Org", slug=f"heal-list-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), fixture_portal_url.split("//")[1])
        script = GoldenScript(
            org_id=org.id, portal_profile_id=profile.id, version=1,
            script_blob_ref="unused", status="active",
        )
        session.add(script)
        session.flush()
        pending = HealEvent(org_id=org.id, golden_script_id=script.id, trigger="on_demand", status="pending_approval")
        approved = HealEvent(org_id=org.id, golden_script_id=script.id, trigger="on_demand", status="approved")
        session.add_all([pending, approved])
        session.flush()
        org_id = str(org.id)
        pending_id = pending.id

    client = TestClient(app)
    response = client.get(f"/heal-events?org_id={org_id}&status=pending_approval")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == str(pending_id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_api_heal_list.py -v`
Expected: FAIL — no matching route for `GET /heal-events`.

- [ ] **Step 3: Implement `list_heal_events`**

Add to `engine/src/atlas_engine/verify/heal.py`:

```python
def list_heal_events(org_id: str, status: str | None = None) -> list[dict]:
    with session_scope() as session:
        query = session.query(HealEvent).filter_by(org_id=uuid.UUID(org_id))
        if status is not None:
            query = query.filter_by(status=status)
        return [
            {
                "id": str(event.id),
                "golden_script_id": str(event.golden_script_id),
                "trigger": event.trigger,
                "status": event.status,
                "diff_summary": event.diff_summary,
                "created_at": event.created_at.isoformat(),
            }
            for event in query.order_by(HealEvent.created_at.desc()).all()
        ]
```

- [ ] **Step 4: Implement the endpoint**

In `engine/src/atlas_engine/api.py`, extend the `verify.heal` import to add `list_heal_events`, then add:

```python
@app.get("/heal-events")
def list_heal_events_endpoint(org_id: str, status: str | None = None) -> list[dict]:
    return list_heal_events(org_id, status=status)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_api_heal_list.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/atlas_engine/verify/heal.py src/atlas_engine/api.py tests/test_api_heal_list.py
git commit -m "feat: add GET /heal-events list endpoint"
```

---

### Task 4: README + finish branch

**Files:**
- Modify: `engine/README.md`

- [ ] **Step 1: Document the two list endpoints**

Add a short note under the existing "Recording requests" or "Video" section (whichever reads more naturally in context) mentioning `GET /recordings` and `GET /heal-events` alongside their existing single-item counterparts.

- [ ] **Step 2: Commit**

```bash
git add engine/README.md
git commit -m "docs: document recordings and heal-events list endpoints"
```

- [ ] **Step 3: Finish the branch**

**REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch — run the full suite, present the merge/PR/keep menu, act on the answer.
