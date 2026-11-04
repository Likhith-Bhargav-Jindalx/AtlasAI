# Desktop Error Tracking (Sentry) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Sentry error tracking into the Windows desktop exe so crashes in the main thread (webview), the background uvicorn thread, and unhandled FastAPI route exceptions are reported in real time instead of being invisible in the windowed, console-less build.

**Architecture:** A new `init_error_tracking()` function in `engine/src/atlas_engine/observability.py` reads `ATLAS_SENTRY_DSN` from the environment and calls `sentry_sdk.init(...)` if set (no-op otherwise). `desktop/app.py` calls it right after it sets up log-file redirection, wraps `main()` in try/except to report main-thread crashes, and relies on Sentry's default `ThreadingIntegration` to auto-capture the uvicorn background thread's exceptions. The existing FastAPI `unhandled_exception_handler` additionally reports to Sentry before returning its generic 500. CI bakes a new `SENTRY_DSN` secret into `production.env`, the same way `ATLAS_DATABASE_URL` etc. are baked in today.

**Tech Stack:** Python 3.11, `sentry-sdk`, FastAPI, pytest, pydantic-settings.

## Global Constraints

- Desktop exe only — do not touch the backend/Container Apps deployment's logging or add APM/OpenTelemetry there (see `docs/superpowers/specs/2026-08-17-backend-production-deployment-design.md`, which deferred that intentionally).
- Sentry cloud SaaS (free tier) — no self-hosted Sentry/GlitchTip infra.
- `sentry_sdk.init(...)` must always pass `send_default_pii=False` and `include_local_variables=False` — the exe bakes in `ATLAS_DATABASE_URL` and `ATLAS_SESSION_SECRET` as env vars, and stack-trace locals must never be allowed to leak them.
- `environment="desktop-exe"` tag on every event.
- No APM/performance tracing, no dashboards beyond Sentry's default error view.
- If `ATLAS_SENTRY_DSN` is unset, every piece of this must be a silent no-op (local dev and CI test runs have no DSN).

---

### Task 1: `init_error_tracking()` in observability.py

**Files:**
- Modify: `engine/pyproject.toml` (add `sentry-sdk` dependency)
- Modify: `engine/src/atlas_engine/observability.py`
- Modify: `engine/tests/test_observability.py`

**Interfaces:**
- Produces: `init_error_tracking(release: str | None = None) -> None` in `atlas_engine.observability` — reads `ATLAS_SENTRY_DSN` from `os.environ`; no-op if unset; otherwise calls `sentry_sdk.init(dsn=..., environment="desktop-exe", release=release, send_default_pii=False, include_local_variables=False)`.

- [ ] **Step 1: Add the dependency**

Edit `engine/pyproject.toml`, in the `dependencies` list, add after `"python-multipart>=0.0.12",`:

```toml
    "sentry-sdk>=2.0",
```

- [ ] **Step 2: Install it**

Run: `cd engine && pip install -e ".[dev]"`
Expected: installs `sentry-sdk` with no errors.

- [ ] **Step 3: Write the failing tests**

Append to `engine/tests/test_observability.py`:

```python
def test_init_error_tracking_is_a_noop_without_a_dsn(monkeypatch):
    monkeypatch.delenv("ATLAS_SENTRY_DSN", raising=False)
    calls = []
    monkeypatch.setattr("sentry_sdk.init", lambda **kwargs: calls.append(kwargs))

    init_error_tracking()

    assert calls == []


def test_init_error_tracking_calls_sentry_init_with_a_dsn(monkeypatch):
    monkeypatch.setenv("ATLAS_SENTRY_DSN", "https://fake@example.ingest.sentry.io/1")
    calls = []
    monkeypatch.setattr("sentry_sdk.init", lambda **kwargs: calls.append(kwargs))

    init_error_tracking(release="1.2.3")

    assert len(calls) == 1
    assert calls[0]["dsn"] == "https://fake@example.ingest.sentry.io/1"
    assert calls[0]["environment"] == "desktop-exe"
    assert calls[0]["release"] == "1.2.3"
    assert calls[0]["send_default_pii"] is False
    assert calls[0]["include_local_variables"] is False
```

And add `init_error_tracking` to the existing import line at the top of the file:

```python
from atlas_engine.observability import _JsonFormatter, configure_logging, get_request_id, init_error_tracking
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd engine && pytest tests/test_observability.py -v -k init_error_tracking`
Expected: FAIL with `ImportError: cannot import name 'init_error_tracking'`

- [ ] **Step 5: Implement `init_error_tracking`**

In `engine/src/atlas_engine/observability.py`, add after `configure_logging`:

```python
def init_error_tracking(release: str | None = None) -> None:
    """Reports unhandled exceptions to Sentry in real time -- a double-
    clicked desktop exe has no console and no one watching it, so without
    this, a crash in the webview main thread, the background uvicorn
    thread, or a FastAPI route is only ever visible (if at all) as a line
    in ~/.atlas/logs/atlas-desktop.log that nobody is tailing.

    No-op if ATLAS_SENTRY_DSN isn't set -- local dev and the test suite
    never set it, so Sentry stays fully inert there.

    include_local_variables=False and send_default_pii=False are not
    defaults to revisit casually: the exe bakes ATLAS_DATABASE_URL and
    ATLAS_SESSION_SECRET into its environment at build time, and a
    captured stack frame's local variables would otherwise be able to leak
    them straight into Sentry."""
    dsn = os.environ.get("ATLAS_SENTRY_DSN")
    if not dsn:
        return

    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment="desktop-exe",
        release=release,
        send_default_pii=False,
        include_local_variables=False,
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd engine && pytest tests/test_observability.py -v`
Expected: PASS, all tests including the two new ones.

- [ ] **Step 7: Commit**

```bash
git add engine/pyproject.toml engine/src/atlas_engine/observability.py engine/tests/test_observability.py
git commit -m "feat: add init_error_tracking() for Sentry desktop crash reporting"
```

---

### Task 2: `sentry_dsn` Settings field + production.env.example

**Files:**
- Modify: `engine/src/atlas_engine/config.py`
- Modify: `engine/tests/test_config.py`
- Modify: `desktop/production.env.example`

**Interfaces:**
- Consumes: none (standalone config field).
- Produces: `Settings.sentry_dsn: str | None` (documents the env var; the desktop entry point itself still reads `os.environ` directly before `Settings` is constructed, same split already used for Azure OpenAI keys — see Task 3).

- [ ] **Step 1: Write the failing test**

Append to `engine/tests/test_config.py`:

```python
def test_settings_sentry_dsn_defaults_to_none(monkeypatch):
    monkeypatch.delenv("ATLAS_SENTRY_DSN", raising=False)
    assert Settings().sentry_dsn is None


def test_settings_sentry_dsn_reads_from_env(monkeypatch):
    monkeypatch.setenv("ATLAS_SENTRY_DSN", "https://fake@example.ingest.sentry.io/1")
    assert Settings().sentry_dsn == "https://fake@example.ingest.sentry.io/1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && pytest tests/test_config.py -v -k sentry_dsn`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'sentry_dsn'`

- [ ] **Step 3: Add the field**

In `engine/src/atlas_engine/config.py`, add to the `Settings` class, after `require_azure_storage: bool = False`:

```python
    sentry_dsn: str | None = None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd engine && pytest tests/test_config.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Document the env var in production.env.example**

Append to `desktop/production.env.example`:

```
ATLAS_SENTRY_DSN=https://examplePublicKey@o0.ingest.sentry.io/0
```

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/config.py engine/tests/test_config.py desktop/production.env.example
git commit -m "feat: add sentry_dsn Settings field and document ATLAS_SENTRY_DSN"
```

---

### Task 3: Wire into FastAPI's unhandled_exception_handler

**Files:**
- Modify: `engine/src/atlas_engine/api.py:1-15` (import), `:80-95` (handler)
- Modify: `engine/tests/test_api_error_handling.py`

**Interfaces:**
- Consumes: `sentry_sdk` (from Task 1's dependency), `get_request_id()` (already imported in `api.py`).
- Produces: none for later tasks — this is the route-level capture point.

- [ ] **Step 1: Write the failing test**

Append to `engine/tests/test_api_error_handling.py`:

```python
def test_unhandled_exception_reports_to_sentry_with_request_id_tag(monkeypatch):
    calls = []
    tags = {}
    monkeypatch.setattr("sentry_sdk.capture_exception", lambda exc: calls.append(exc))
    monkeypatch.setattr("sentry_sdk.set_tag", lambda key, value: tags.__setitem__(key, value))

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/author",
        json={"org_id": "not-a-uuid", "start_url": "http://example.com", "actions": []},
    )

    assert response.status_code == 500
    assert len(calls) == 1
    assert tags["request_id"] == response.headers["x-request-id"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd engine && pytest tests/test_api_error_handling.py -v -k sentry`
Expected: FAIL — `calls` is empty (assert `len(calls) == 1` fails with `0 == 1`).

- [ ] **Step 3: Implement the capture call**

In `engine/src/atlas_engine/api.py`, add `import sentry_sdk` to the top-of-file import block (next to the other third-party imports, before the `from atlas_engine.observability import ...` line):

```python
import sentry_sdk
```

Then in `unhandled_exception_handler` (currently lines 80-95), add the two calls right after the existing `logger.exception(...)` line:

```python
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    from fastapi.responses import JSONResponse

    request_id = get_request_id()
    if not request_id:
        # Fallback to same logic as middleware if context var not available (e.g., sync func in thread)
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())

    logger.exception("Unhandled exception handling %s %s", request.method, request.url.path)
    sentry_sdk.set_tag("request_id", request_id)
    sentry_sdk.capture_exception(exc)
    response = JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "request_id": request_id},
    )
    response.headers["X-Request-Id"] = request_id
    return response
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd engine && pytest tests/test_api_error_handling.py -v`
Expected: PASS, all tests (the pre-existing clean-JSON test and the new Sentry test).

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `cd engine && pytest -q`
Expected: PASS, no new failures. (`sentry_sdk.capture_exception`/`set_tag` are no-ops with no DSN configured, matching every other test in the suite.)

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/api.py engine/tests/test_api_error_handling.py
git commit -m "feat: report unhandled FastAPI exceptions to Sentry with request_id tag"
```

---

### Task 4: Wire into desktop/app.py (main thread + startup)

**Files:**
- Modify: `desktop/app.py`

**Interfaces:**
- Consumes: `init_error_tracking(release: str | None = None)` from `atlas_engine.observability` (Task 1).
- Produces: none for later tasks.

- [ ] **Step 1: Move `_read_app_version` above the early-setup calls**

In `desktop/app.py`, this function currently lives at lines 104-105, after the `_load_production_env()` / `_configure_log_file()` calls it needs to run alongside:

```python
def _read_app_version() -> str:
    return _resource_path("VERSION").read_text().strip()
```

Delete it from its current location (lines 104-105) and re-add it directly below `_resource_path` (currently lines 34-39), so it reads:

```python
def _resource_path(relative: str) -> Path:
    """Resolves a bundled resource both when run unfrozen (dev testing,
    relative to this file) and when frozen by PyInstaller (relative to
    sys._MEIPASS, the bundle's extraction dir)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def _read_app_version() -> str:
    return _resource_path("VERSION").read_text().strip()
```

- [ ] **Step 2: Call `init_error_tracking()` after log-file setup**

Change:

```python
_load_production_env()
_configure_log_file()

import dataclasses
```

to:

```python
_load_production_env()
_configure_log_file()

from atlas_engine.observability import init_error_tracking

init_error_tracking(release=_read_app_version())

import dataclasses
```

- [ ] **Step 3: Wrap `main()`'s body to report startup/webview crashes**

Change:

```python
def main() -> None:
    _unblock_windows_bundle()
    frontend_dist = _resource_path("frontend_dist")

    # Mounted after the app's own routes are already registered at import
    # time, so /health, /auth/login, etc. still win — this / mount only
    # catches what no route matched (the SPA's static assets).
    fastapi_app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    threading.Thread(target=_run_server, daemon=True).start()
    time.sleep(1.5)

    # "localhost", not "127.0.0.1" — the frontend's compiled JS calls
    # http://localhost:8000 by default (VITE_API_BASE_URL's fallback), and
    # browsers treat 127.0.0.1 and localhost as different origins even
    # though they resolve to the same machine. Loading the window from a
    # different origin than the frontend's own fetch calls turns every API
    # request into a cross-origin one the CORS allowlist doesn't cover.
    webview.create_window("Atlas AI", f"http://localhost:{PORT}/", width=1280, height=800)
    webview.start()
```

to:

```python
def main() -> None:
    try:
        _unblock_windows_bundle()
        frontend_dist = _resource_path("frontend_dist")

        # Mounted after the app's own routes are already registered at import
        # time, so /health, /auth/login, etc. still win — this / mount only
        # catches what no route matched (the SPA's static assets).
        fastapi_app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

        threading.Thread(target=_run_server, daemon=True).start()
        time.sleep(1.5)

        # "localhost", not "127.0.0.1" — the frontend's compiled JS calls
        # http://localhost:8000 by default (VITE_API_BASE_URL's fallback), and
        # browsers treat 127.0.0.1 and localhost as different origins even
        # though they resolve to the same machine. Loading the window from a
        # different origin than the frontend's own fetch calls turns every API
        # request into a cross-origin one the CORS allowlist doesn't cover.
        webview.create_window("Atlas AI", f"http://localhost:{PORT}/", width=1280, height=800)
        webview.start()
    except Exception as exc:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
        raise
```

- [ ] **Step 4: Verify the module still imports cleanly**

Run: `cd engine && python -c "import ast; ast.parse(open('../desktop/app.py').read())"`
Expected: no output, exits 0 (confirms no syntax errors before a full frozen build is tried).

Run: `ATLAS_DATABASE_URL=postgresql+psycopg://atlas:atlas_dev_password@localhost:5434/atlas_test ATLAS_AZURE_STORAGE_CONNECTION_STRING= ATLAS_REQUIRE_AZURE_STORAGE=false python3 -c "
import sys
sys.path.insert(0, 'engine/src')
sys.path.insert(0, 'desktop')
import app
assert app._read_app_version() == '1.0.0'
print('ok')
"`
Expected: prints `ok` (confirms `_read_app_version` works from its new location and `app.py` still imports end-to-end outside a frozen build).

- [ ] **Step 5: Manual verification before the next real release (not automated)**

Set a real Sentry DSN, build the exe (`desktop/build.sh`), run it with `ATLAS_SENTRY_DSN` present in `production.env`, trigger a route that 500s (e.g. the existing `/author` bad-org-id case from Task 3's test), and confirm the event shows up in the Sentry project dashboard tagged `environment=desktop-exe` with the correct `release`. Confirm no `database_url`/`session_secret` values appear anywhere in the event payload.

- [ ] **Step 6: Commit**

```bash
git add desktop/app.py
git commit -m "feat: report desktop exe startup/webview crashes to Sentry"
```

---

### Task 5: CI — bake ATLAS_SENTRY_DSN into production.env

**Files:**
- Modify: `.github/workflows/build-exe.yml`

**Interfaces:**
- Consumes: a new GitHub Actions secret `SENTRY_DSN` (the human running CI must add this to the repo's `production` environment secrets before this task's change takes effect — this task only wires the workflow, it cannot create the secret itself).
- Produces: none.

- [ ] **Step 1: Add the secret to the production.env heredoc**

In `.github/workflows/build-exe.yml`, change the `Write baked-in production credentials` step from:

```yaml
      - name: Write baked-in production credentials
        shell: bash
        # This exe talks directly to production Postgres and Blob Storage —
        # no deployed backend in between — so the credentials it needs at
        # runtime are baked into the bundle at build time instead of being
        # supplied by whoever runs it. Written from repo secrets, never
        # committed (desktop/production.env is gitignored).
        run: |
          cat > desktop/production.env <<EOF
          ATLAS_DATABASE_URL=${{ secrets.ATLAS_DATABASE_URL }}
          ATLAS_SESSION_SECRET=${{ secrets.ATLAS_SESSION_SECRET }}
          ATLAS_AZURE_STORAGE_CONNECTION_STRING=${{ secrets.ATLAS_AZURE_STORAGE_CONNECTION_STRING }}
          ATLAS_AZURE_STORAGE_CONTAINER=atlas-runs
          ATLAS_REQUIRE_AZURE_STORAGE=true
          EOF
```

to:

```yaml
      - name: Write baked-in production credentials
        shell: bash
        # This exe talks directly to production Postgres and Blob Storage —
        # no deployed backend in between — so the credentials it needs at
        # runtime are baked into the bundle at build time instead of being
        # supplied by whoever runs it. Written from repo secrets, never
        # committed (desktop/production.env is gitignored).
        run: |
          cat > desktop/production.env <<EOF
          ATLAS_DATABASE_URL=${{ secrets.ATLAS_DATABASE_URL }}
          ATLAS_SESSION_SECRET=${{ secrets.ATLAS_SESSION_SECRET }}
          ATLAS_AZURE_STORAGE_CONNECTION_STRING=${{ secrets.ATLAS_AZURE_STORAGE_CONNECTION_STRING }}
          ATLAS_AZURE_STORAGE_CONTAINER=atlas-runs
          ATLAS_REQUIRE_AZURE_STORAGE=true
          ATLAS_SENTRY_DSN=${{ secrets.SENTRY_DSN }}
          EOF
```

- [ ] **Step 2: Validate the YAML**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/build-exe.yml'))" `
Expected: no output, exits 0 (confirms the heredoc edit didn't break YAML parsing).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/build-exe.yml
git commit -m "ci: bake ATLAS_SENTRY_DSN into the desktop exe's production.env"
```

- [ ] **Step 4: Note for the human merging this**

Before (or right after) this merges to `main`, add a `SENTRY_DSN` secret to the repo's `production` GitHub Actions environment (same place `ATLAS_DATABASE_URL` etc. live today), using the DSN from a new Sentry project created for Atlas Desktop. Until that secret exists, `${{ secrets.SENTRY_DSN }}` resolves to an empty string, `ATLAS_SENTRY_DSN` is empty in the baked `production.env`, and `init_error_tracking()` stays a no-op — safe, just inert.

---

## Self-Review Notes

- Spec coverage: architecture/init (Task 1), config field (Task 2), FastAPI route capture (Task 3), main-thread/webview capture (Task 4), background-thread capture (covered by Sentry's default `ThreadingIntegration`, enabled automatically by `sentry_sdk.init` in Task 1 — no separate task needed), CI wiring (Task 5), secret safety (`include_local_variables=False`/`send_default_pii=False` enforced in Task 1's implementation and asserted in its test), breadcrumbs (Sentry's default `LoggingIntegration`, automatic, no task needed), manual pre-release dashboard check (Task 4 Step 5). CLI capture point is explicitly a non-goal per the spec (CLI is local-dev-only, not wired to `ATLAS_SENTRY_DSN` packaging) — no task for it.
- No placeholders: every step has exact code, exact file paths, exact commands.
- Type consistency: `init_error_tracking(release: str | None = None)` defined in Task 1 matches its call site `init_error_tracking(release=_read_app_version())` in Task 4, and its no-DSN/with-DSN behavior matches both its own tests and the manual verification step.
