# Auth & Live Recording Backend Prerequisites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the two backend capabilities the frontend needs before it can be real: a minimal login/session mechanism, and start/stop endpoints so a "Record" button can drive a live headed-browser capture across two separate HTTP requests.

**Architecture:** A new `auth` package (password hashing + a hand-rolled signed session token — no new heavyweight dependency for something this simple) plus login/logout/me endpoints in `api.py`. A new `live_capture` module wraps the existing, unmodified `CaptureSession`/`persist_recording` in a background thread coordinated via `threading.Event`, so the only thing crossing between the request thread and the thread that owns the Playwright sync objects is a signal — never a Playwright object itself. Per `2026-08-12-frontend-foundation-design.md` §3–4.

**Tech Stack:** Same as before (Python, FastAPI, Click, pytest, Playwright) plus `bcrypt` for password hashing — the only new dependency.

## Global Constraints

- Existing endpoints are **not** retrofitted to require the new session — `/recording-requests`, `/recordings/{id}/author`, heal endpoints, etc. keep taking explicit `org_id`/`user_id` exactly as today. This plan is purely additive.
- The background thread in `live_capture.py` is the only code that ever touches Playwright sync-API objects for a live session; the request-handling thread only ever signals it via `threading.Event`. This is a hard rule, not a style preference — Phase 0 already hit a real deadlock from violating this kind of boundary once (`engine/src/atlas_engine/capture/recorder.py`'s design note).
- No new dependency beyond `bcrypt`. The session token is hand-rolled HMAC signing over stdlib `hmac`/`hashlib`/`base64`/`json` — deliberately not another third-party JWT/session library for something this small.
- Every step ends with tests passing and a commit. Do not proceed to the next task with failing tests.

---

## File Structure

```
engine/src/atlas_engine/
├── auth/
│   ├── __init__.py
│   ├── passwords.py        # NEW — hash_password, verify_password
│   └── session.py          # NEW — create_session_token, verify_session_token
├── live_capture.py          # NEW — start_live_capture, stop_live_capture
├── config.py                 # MODIFIED — settings.session_secret
├── cli.py                    # MODIFIED — `atlas create-user` command
└── api.py                    # MODIFIED — /auth/*, /recordings/start, /recordings/{id}/stop

engine/tests/
├── test_auth_passwords.py
├── test_auth_session.py
├── test_cli_create_user.py
├── test_api_auth.py
├── test_live_capture.py
├── test_api_live_recording.py
└── test_end_to_end_auth_and_recording.py
```

---

### Task 1: Password hashing + `atlas create-user`

**Files:**
- Create: `engine/src/atlas_engine/auth/__init__.py`
- Create: `engine/src/atlas_engine/auth/passwords.py`
- Modify: `engine/pyproject.toml` (add `bcrypt`)
- Modify: `engine/src/atlas_engine/cli.py`
- Create: `engine/tests/test_auth_passwords.py`
- Create: `engine/tests/test_cli_create_user.py`

**Interfaces:**
- Produces: `hash_password(password: str) -> str`; `verify_password(password: str, password_hash: str) -> bool`; `atlas create-user --org-id --email --password --role --job-function` (dev-only user creation, no signup flow).

- [ ] **Step 1: Add `bcrypt` to `engine/pyproject.toml`**

In the `dependencies` list, add:
```toml
    "bcrypt>=4.0",
```

Run: `pip install -e ".[dev]"` (from `engine/`, with the venv active) to install it.

- [ ] **Step 2: Write the failing test**

`engine/tests/test_auth_passwords.py`:
```python
from atlas_engine.auth.passwords import hash_password, verify_password


def test_hash_and_verify_password_round_trips():
    hashed = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong password", hashed) is False


def test_hash_password_salts_differently_each_time():
    first = hash_password("same-password")
    second = hash_password("same-password")

    assert first != second
    assert verify_password("same-password", first) is True
    assert verify_password("same-password", second) is True
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_auth_passwords.py -v`
Expected: `FAIL` with `ModuleNotFoundError: No module named 'atlas_engine.auth'`.

- [ ] **Step 4: Write `engine/src/atlas_engine/auth/passwords.py`**

```python
import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
```

- [ ] **Step 5: Write `engine/src/atlas_engine/auth/__init__.py`**

```python
from atlas_engine.auth.passwords import hash_password, verify_password

__all__ = ["hash_password", "verify_password"]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_auth_passwords.py -v`
Expected: `PASS` on both tests.

- [ ] **Step 7: Write the failing test for `create-user`**

`engine/tests/test_cli_create_user.py`:
```python
import uuid

from click.testing import CliRunner

from atlas_engine.auth.passwords import verify_password
from atlas_engine.cli import main
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, User


def test_create_user_command_creates_a_user_with_hashed_password():
    with session_scope() as session:
        org = Organization(name="Create User Org", slug=f"create-user-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    email = f"newuser-{uuid.uuid4()}@example.com"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "create-user",
            "--org-id", org_id,
            "--email", email,
            "--password", "hunter2-but-better",
            "--role", "member",
            "--job-function", "developer",
        ],
    )

    assert result.exit_code == 0, result.output

    with session_scope() as session:
        user = session.query(User).filter_by(email=email).one()
        assert user.role == "member"
        assert user.job_function == "developer"
        assert user.password_hash != "hunter2-but-better"
        assert verify_password("hunter2-but-better", user.password_hash) is True
```

- [ ] **Step 8: Run test to verify it fails**

Run: `pytest tests/test_cli_create_user.py -v`
Expected: `FAIL` — `Error: No such command 'create-user'.`

- [ ] **Step 9: Add the command to `engine/src/atlas_engine/cli.py`**

Add this import:
```python
from atlas_engine.auth.passwords import hash_password
from atlas_engine.db.models import User
```

Append (before `if __name__ == "__main__":`):
```python
@main.command("create-user")
@click.option("--org-id", required=True)
@click.option("--email", required=True)
@click.option("--password", required=True)
@click.option("--role", default="member", type=click.Choice(["owner", "admin", "member"]))
@click.option("--job-function", default=None, type=click.Choice(["operations_agent", "developer"]))
def create_user(org_id: str, email: str, password: str, role: str, job_function: str | None) -> None:
    """Dev-only user creation — no signup flow exists yet."""
    with session_scope() as session:
        user = User(
            org_id=uuid.UUID(org_id),
            email=email,
            password_hash=hash_password(password),
            role=role,
            job_function=job_function,
        )
        session.add(user)
        session.flush()
        click.echo(f"User created: {user.id}")
```

- [ ] **Step 10: Run test to verify it passes**

Run: `pytest tests/test_cli_create_user.py -v`
Expected: `PASS`.

- [ ] **Step 11: Run the full suite**

Run: `pytest -v`
Expected: `PASS` on everything.

- [ ] **Step 12: Commit**

```bash
git add engine/pyproject.toml engine/src/atlas_engine/auth engine/src/atlas_engine/cli.py \
        engine/tests/test_auth_passwords.py engine/tests/test_cli_create_user.py
git commit -m "feat: add password hashing and atlas create-user command"
```

---

### Task 2: Signed session tokens

**Files:**
- Create: `engine/src/atlas_engine/auth/session.py`
- Modify: `engine/src/atlas_engine/auth/__init__.py`
- Modify: `engine/src/atlas_engine/config.py`
- Create: `engine/tests/test_auth_session.py`

**Interfaces:**
- Produces: `settings.session_secret: str` (env `ATLAS_SESSION_SECRET`, defaults to an obviously-insecure dev value); `create_session_token(data: dict, *, max_age_seconds: int = 604800) -> str`; `verify_session_token(token: str) -> dict | None` (returns `None` on any tampering, malformed input, or expiry — never raises).

- [ ] **Step 1: Write the failing test**

`engine/tests/test_auth_session.py`:
```python
from atlas_engine.auth.session import create_session_token, verify_session_token


def test_create_and_verify_session_token_round_trips():
    token = create_session_token({"user_id": "abc", "org_id": "def"})

    payload = verify_session_token(token)

    assert payload["user_id"] == "abc"
    assert payload["org_id"] == "def"


def test_verify_session_token_rejects_a_tampered_token():
    token = create_session_token({"user_id": "abc"})
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    assert verify_session_token(tampered) is None


def test_verify_session_token_rejects_an_expired_token():
    token = create_session_token({"user_id": "abc"}, max_age_seconds=-10)

    assert verify_session_token(token) is None


def test_verify_session_token_rejects_malformed_input():
    assert verify_session_token("not-a-real-token") is None
    assert verify_session_token("") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth_session.py -v`
Expected: `FAIL` with `ModuleNotFoundError: No module named 'atlas_engine.auth.session'`.

- [ ] **Step 3: Add `session_secret` to `engine/src/atlas_engine/config.py`**

Add this field to the `Settings` class, alongside `run_root`:
```python
    session_secret: str = "dev-insecure-secret-change-me-via-ATLAS_SESSION_SECRET"
```

- [ ] **Step 4: Write `engine/src/atlas_engine/auth/session.py`**

```python
import base64
import hashlib
import hmac
import json
import time

from atlas_engine.config import settings


def _sign(payload_b64: bytes) -> str:
    signature = hmac.new(settings.session_secret.encode("utf-8"), payload_b64, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")


def create_session_token(data: dict, *, max_age_seconds: int = 60 * 60 * 24 * 7) -> str:
    payload = {**data, "exp": int(time.time()) + max_age_seconds}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).rstrip(b"=")
    signature = _sign(payload_b64)
    return f"{payload_b64.decode('ascii')}.{signature}"


def verify_session_token(token: str) -> dict | None:
    """Never raises — any malformed, tampered, or expired token just
    returns None, so callers can treat every failure mode identically."""
    try:
        payload_b64_str, signature = token.split(".", 1)
    except ValueError:
        return None

    payload_b64 = payload_b64_str.encode("ascii")
    expected_signature = _sign(payload_b64)
    if not hmac.compare_digest(signature, expected_signature):
        return None

    padded = payload_b64 + b"=" * (-len(payload_b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None

    if payload.get("exp", 0) < time.time():
        return None

    return payload
```

- [ ] **Step 5: Update `engine/src/atlas_engine/auth/__init__.py`**

```python
from atlas_engine.auth.passwords import hash_password, verify_password
from atlas_engine.auth.session import create_session_token, verify_session_token

__all__ = ["create_session_token", "hash_password", "verify_password", "verify_session_token"]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_auth_session.py -v`
Expected: `PASS` on all four tests.

- [ ] **Step 7: Run the full suite**

Run: `pytest -v`
Expected: `PASS` on everything.

- [ ] **Step 8: Commit**

```bash
git add engine/src/atlas_engine/auth engine/src/atlas_engine/config.py engine/tests/test_auth_session.py
git commit -m "feat: add hand-rolled signed session tokens"
```

---

### Task 3: Login / logout / me endpoints

**Files:**
- Modify: `engine/src/atlas_engine/api.py`
- Create: `engine/tests/test_api_auth.py`

**Interfaces:**
- Produces: `POST /auth/login` (body: `email`, `password`; sets an `httpOnly` cookie named `atlas_session`; returns `{user_id, org_id, email, role, job_function}` or `401`); `POST /auth/logout` (clears the cookie); `GET /auth/me` (reads the cookie, returns the same identity shape or `401`).

- [ ] **Step 1: Write the failing test**

`engine/tests/test_api_auth.py`:
```python
import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.auth.passwords import hash_password
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, User


def _make_user(email: str, password: str) -> None:
    with session_scope() as session:
        org = Organization(name="Auth API Org", slug=f"auth-api-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        session.add(
            User(org_id=org.id, email=email, password_hash=hash_password(password), role="member")
        )


def test_login_sets_cookie_and_me_returns_identity():
    email = f"login-test-{uuid.uuid4()}@example.com"
    _make_user(email, "correct-password")

    client = TestClient(app)
    login_response = client.post("/auth/login", json={"email": email, "password": "correct-password"})

    assert login_response.status_code == 200
    assert login_response.json()["email"] == email
    assert "atlas_session" in login_response.cookies

    me_response = client.get("/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == email


def test_login_rejects_wrong_password():
    email = f"wrong-pw-{uuid.uuid4()}@example.com"
    _make_user(email, "correct-password")

    client = TestClient(app)
    response = client.post("/auth/login", json={"email": email, "password": "wrong-password"})

    assert response.status_code == 401


def test_login_rejects_unknown_email():
    client = TestClient(app)
    response = client.post("/auth/login", json={"email": "nobody@example.com", "password": "x"})

    assert response.status_code == 401


def test_me_without_a_session_returns_401():
    client = TestClient(app)
    response = client.get("/auth/me")

    assert response.status_code == 401


def test_logout_clears_the_session():
    email = f"logout-test-{uuid.uuid4()}@example.com"
    _make_user(email, "correct-password")

    client = TestClient(app)
    client.post("/auth/login", json={"email": email, "password": "correct-password"})

    logout_response = client.post("/auth/logout")
    assert logout_response.status_code == 200

    me_response = client.get("/auth/me")
    assert me_response.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_auth.py -v`
Expected: `FAIL` with `404 Not Found` on all endpoints.

- [ ] **Step 3: Add the endpoints to `engine/src/atlas_engine/api.py`**

Add these imports:
```python
from fastapi import Cookie, HTTPException, Response

from atlas_engine.auth.passwords import verify_password
from atlas_engine.auth.session import create_session_token, verify_session_token
from atlas_engine.db.models import User

SESSION_COOKIE_NAME = "atlas_session"
```

Append:
```python
class LoginBody(BaseModel):
    email: str
    password: str


class MeResponse(BaseModel):
    user_id: str
    org_id: str
    email: str
    role: str
    job_function: str | None = None


def _me_response_for(user: User) -> MeResponse:
    return MeResponse(
        user_id=str(user.id), org_id=str(user.org_id), email=user.email,
        role=user.role, job_function=user.job_function,
    )


@app.post("/auth/login", response_model=MeResponse)
def login(body: LoginBody, response: Response) -> MeResponse:
    with session_scope() as session:
        user = session.query(User).filter_by(email=body.email).one_or_none()
        if user is None or not verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")

        token = create_session_token({"user_id": str(user.id), "org_id": str(user.org_id)})
        response.set_cookie(SESSION_COOKIE_NAME, token, httponly=True, samesite="lax")
        return _me_response_for(user)


@app.post("/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@app.get("/auth/me", response_model=MeResponse)
def me(atlas_session: str | None = Cookie(default=None)) -> MeResponse:
    if atlas_session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = verify_session_token(atlas_session)
    if payload is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    with session_scope() as session:
        user = session.get(User, uuid.UUID(payload["user_id"]))
        if user is None:
            raise HTTPException(status_code=401, detail="User no longer exists")
        return _me_response_for(user)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_api_auth.py -v`
Expected: `PASS` on all five tests.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: `PASS` on everything.

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/api.py engine/tests/test_api_auth.py
git commit -m "feat: add login/logout/me endpoints"
```

---

### Task 4: Live capture registry

**Files:**
- Create: `engine/src/atlas_engine/live_capture.py`
- Create: `engine/tests/test_live_capture.py`

**Interfaces:**
- Consumes: `CaptureSession` (Phase 0, unmodified), `persist_recording` (recording-requests plan, unmodified).
- Produces: `start_live_capture(org_id: str, start_url: str, *, request_id: uuid.UUID | None = None, role: str = "demo") -> uuid.UUID` (returns a `capture_session_id` immediately — the browser launches in the background); `stop_live_capture(capture_session_id: uuid.UUID, *, timeout: float = 30.0) -> uuid.UUID` (blocks until the background thread finishes closing the browser and persisting, returns the new `recording_id`; raises `ValueError` for an unknown session id, re-raises whatever the background thread raised on failure).

**Note:** this test launches a real headed (`headless=False`) Chromium window, same as `atlas record` — expect a visible browser window to flash open and close during this test run. This is correct, not a bug: the whole feature is "let a human interact with a real visible browser," so the test exercises that for real rather than mocking it away.

- [ ] **Step 1: Write the failing test**

`engine/tests/test_live_capture.py`:
```python
import uuid

import pytest

from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, Recording
from atlas_engine.live_capture import start_live_capture, stop_live_capture


def test_start_and_stop_live_capture_persists_a_recording(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Live Capture Org", slug=f"live-capture-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"

    capture_session_id = start_live_capture(org_id, start_url)
    recording_id = stop_live_capture(capture_session_id)

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        assert recording.status == "captured"
        assert recording.role == "demo"


def test_stop_live_capture_raises_for_an_unknown_session():
    with pytest.raises(ValueError, match="No active capture session"):
        stop_live_capture(uuid.uuid4())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_live_capture.py -v`
Expected: `FAIL` with `ModuleNotFoundError: No module named 'atlas_engine.live_capture'`.

- [ ] **Step 3: Write `engine/src/atlas_engine/live_capture.py`**

```python
import threading
import uuid

from playwright.sync_api import sync_playwright

from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.recording_service import persist_recording

_sessions: dict[uuid.UUID, "_LiveCaptureSession"] = {}
_registry_lock = threading.Lock()


class _LiveCaptureSession:
    """Owns the Playwright sync-API objects for the lifetime of one live
    recording. Everything outside this class communicates with it only
    through stop_event/done_event — never by touching Playwright objects
    from another thread, which is what makes this safe."""

    def __init__(self, org_id: str, start_url: str, request_id: uuid.UUID | None, role: str):
        self.org_id = org_id
        self.start_url = start_url
        self.request_id = request_id
        self.role = role
        self.stop_event = threading.Event()
        self.done_event = threading.Event()
        self.recording_id: uuid.UUID | None = None
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=False)
                page = browser.new_page()
                page.goto(self.start_url)
                with CaptureSession(page) as capture:
                    self.stop_event.wait()
                actions = capture.actions
                browser.close()

            self.recording_id = persist_recording(
                self.org_id, self.start_url, actions,
                request_id=self.request_id, role=self.role,
            )
        except Exception as exc:  # surfaced to the caller of stop()
            self.error = exc
        finally:
            self.done_event.set()

    def start(self) -> None:
        self.thread.start()

    def stop(self, timeout: float = 30.0) -> uuid.UUID:
        self.stop_event.set()
        completed = self.done_event.wait(timeout=timeout)
        if not completed:
            raise TimeoutError("Capture session did not finish stopping in time")
        if self.error is not None:
            raise self.error
        return self.recording_id


def start_live_capture(
    org_id: str, start_url: str, *, request_id: uuid.UUID | None = None, role: str = "demo"
) -> uuid.UUID:
    session_id = uuid.uuid4()
    session = _LiveCaptureSession(org_id, start_url, request_id, role)
    with _registry_lock:
        _sessions[session_id] = session
    session.start()
    return session_id


def stop_live_capture(capture_session_id: uuid.UUID, *, timeout: float = 30.0) -> uuid.UUID:
    with _registry_lock:
        session = _sessions.pop(capture_session_id, None)
    if session is None:
        raise ValueError(f"No active capture session: {capture_session_id}")
    return session.stop(timeout=timeout)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_live_capture.py -v`
Expected: `PASS` on both tests. A headed Chromium window will visibly open and close — this is expected.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: `PASS` on everything.

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/live_capture.py engine/tests/test_live_capture.py
git commit -m "feat: add live capture registry for start/stop recording via API"
```

---

### Task 5: Live recording API endpoints

**Files:**
- Modify: `engine/src/atlas_engine/api.py`
- Create: `engine/tests/test_api_live_recording.py`

**Interfaces:**
- Produces: `POST /recordings/start` (body: `org_id`, `start_url`, `request_id?`, `role?`; returns `{capture_session_id}`); `POST /recordings/{capture_session_id}/stop` (returns `{recording_id}`).

- [ ] **Step 1: Write the failing test**

`engine/tests/test_api_live_recording.py`:
```python
import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, Recording


def test_start_and_stop_recording_endpoints(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="API Live Recording Org", slug=f"api-live-recording-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    client = TestClient(app)

    start_response = client.post(
        "/recordings/start",
        json={"org_id": org_id, "start_url": f"{fixture_portal_url}/login.html"},
    )
    assert start_response.status_code == 200
    capture_session_id = start_response.json()["capture_session_id"]

    stop_response = client.post(f"/recordings/{capture_session_id}/stop")
    assert stop_response.status_code == 200
    recording_id = stop_response.json()["recording_id"]

    with session_scope() as session:
        recording = session.get(Recording, uuid.UUID(recording_id))
        assert recording.status == "captured"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_live_recording.py -v`
Expected: `FAIL` with `404 Not Found`.

- [ ] **Step 3: Add the endpoints to `engine/src/atlas_engine/api.py`**

Add this import:
```python
from atlas_engine.live_capture import start_live_capture, stop_live_capture
```

Append:
```python
class StartRecordingBody(BaseModel):
    org_id: str
    start_url: str
    request_id: str | None = None
    role: str = "demo"


class StartRecordingResponse(BaseModel):
    capture_session_id: str


class StopRecordingResponse(BaseModel):
    recording_id: str


@app.post("/recordings/start", response_model=StartRecordingResponse)
def start_recording_endpoint(body: StartRecordingBody) -> StartRecordingResponse:
    capture_session_id = start_live_capture(
        body.org_id, body.start_url,
        request_id=uuid.UUID(body.request_id) if body.request_id else None,
        role=body.role,
    )
    return StartRecordingResponse(capture_session_id=str(capture_session_id))


@app.post("/recordings/{capture_session_id}/stop", response_model=StopRecordingResponse)
def stop_recording_endpoint(capture_session_id: str) -> StopRecordingResponse:
    recording_id = stop_live_capture(uuid.UUID(capture_session_id))
    return StopRecordingResponse(recording_id=str(recording_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_api_live_recording.py -v`
Expected: `PASS`.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: `PASS` on everything.

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/api.py engine/tests/test_api_live_recording.py
git commit -m "feat: add /recordings/start and /recordings/{id}/stop API endpoints"
```

---

### Task 6: Capstone — login, then record end to end via the API alone

**Files:**
- Create: `engine/tests/test_end_to_end_auth_and_recording.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5. No new production code.

- [ ] **Step 1: Write the capstone test**

`engine/tests/test_end_to_end_auth_and_recording.py`:
```python
import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.auth.passwords import hash_password
from atlas_engine.authoring_service import author_from_recording
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, Organization, User


def test_login_then_record_and_author_using_only_the_api(fixture_portal_url):
    """Everything a browser-based frontend would actually do, with no
    direct engine imports for the recording step — only HTTP calls plus
    the one function (author_from_recording) the frontend would also call
    via its own /recordings/{id}/author endpoint (already covered
    elsewhere; this test focuses on proving login + live recording work
    together end to end)."""
    email = f"e2e-auth-{uuid.uuid4()}@example.com"
    with session_scope() as session:
        org = Organization(name="E2E Auth Org", slug=f"e2e-auth-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        session.add(User(org_id=org.id, email=email, password_hash=hash_password("test-password"), role="member"))
        org_id = str(org.id)

    client = TestClient(app)

    login_response = client.post("/auth/login", json={"email": email, "password": "test-password"})
    assert login_response.status_code == 200

    me_response = client.get("/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["org_id"] == org_id

    start_url = f"{fixture_portal_url}/login.html"
    start_response = client.post("/recordings/start", json={"org_id": org_id, "start_url": start_url})
    capture_session_id = start_response.json()["capture_session_id"]

    stop_response = client.post(f"/recordings/{capture_session_id}/stop")
    recording_id = stop_response.json()["recording_id"]

    result = author_from_recording(uuid.UUID(recording_id))
    # The unattended scripted "recording" above never touches the login
    # form or the report button, so this trajectory won't certify — that's
    # expected and fine; the point of this test is proving login + live
    # recording work together, not that an empty recording authors cleanly.
    assert result["status"] in ("draft", "certified")

    logout_response = client.post("/auth/logout")
    assert logout_response.status_code == 200

    me_after_logout = client.get("/auth/me")
    assert me_after_logout.status_code == 401
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_end_to_end_auth_and_recording.py -v`
Expected: `PASS`. If `author_from_recording` raises instead of returning `draft`/`certified` (e.g. because an empty trajectory can't even emit), adjust the test's assertion to catch that specific exception type instead — the goal is proving the login+recording plumbing works, not exercising authoring with zero captured steps in a particular way.

- [ ] **Step 3: Run the entire suite one final time**

Run: `pytest -v`
Expected: `PASS` on every test file from Phase 0, Phase 1, recording-requests, and this plan.

- [ ] **Step 4: Commit**

```bash
git add engine/tests/test_end_to_end_auth_and_recording.py
git commit -m "test: add login-and-live-recording capstone test"
```

---

### Task 7: README updates

**Files:**
- Modify: `engine/README.md`

- [ ] **Step 1: Add a "Users, login, and live recording" section**

Insert after the "Recording requests" section:

```markdown
## Users, login, and live recording

Create a user (dev-only — there's no signup flow yet):
```bash
atlas create-user --org-id <id> --email you@example.com --password <password> \
  --role member --job-function developer
```

The local API now has real login:
```
POST /auth/login   {"email": ..., "password": ...}  -> sets an httpOnly session cookie
GET  /auth/me                                        -> current identity, or 401
POST /auth/logout                                    -> clears the cookie
```

Existing endpoints (`/recording-requests`, `/recordings/{id}/author`, heal
endpoints) still take explicit `org_id`/`user_id` in their bodies — login
isn't enforced on them yet, it just gives the frontend a real "who am I."

Live recording — start a headed browser and stop it later, instead of the
CLI's blocking `atlas record`:
```
POST /recordings/start   {"org_id": ..., "start_url": ..., "request_id"?: ..., "role"?: "demo"}
                          -> {"capture_session_id": ...}
POST /recordings/{capture_session_id}/stop
                          -> {"recording_id": ...}
```

Set `ATLAS_SESSION_SECRET` to something real outside local dev — it
defaults to an intentionally obvious placeholder.
```

- [ ] **Step 2: Commit**

```bash
git add engine/README.md
git commit -m "docs: document login and live recording endpoints"
```

---

## Self-Review Notes

**Spec coverage:** minimal auth with the explicit non-retrofit boundary (design spec §3) → Tasks 1–3; live recording via a background thread that never lets the request thread touch Playwright objects directly (design spec §4) → Tasks 4–5; proof the two work together (design spec's whole premise — a human logging in and recording through the browser) → Task 6. Deferred on purpose, unchanged from the design doc: retrofitting existing endpoints to require the session, any route/action-level authorization, and everything about the frontend itself (a separate plan).

**Placeholder scan:** no TBD/TODO markers; every step has literal code. Task 6's one contingency note ("if `author_from_recording` raises instead...") gives a concrete alternative assertion, not an open-ended instruction.

**Type consistency check:** `create_session_token`/`verify_session_token` signatures (Task 2) are used identically in Task 3's login/me endpoints. `start_live_capture`/`stop_live_capture` signatures (Task 4) are used identically in Task 5's API endpoints and Task 6's capstone. The `MeResponse` shape (Task 3) is constructed once via `_me_response_for` and reused by both `/auth/login` and `/auth/me`, so the two can't drift apart.
