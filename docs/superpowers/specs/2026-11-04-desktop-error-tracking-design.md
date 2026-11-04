# Desktop error tracking (Sentry)

## Problem

The Windows desktop exe has no real-time error visibility. The only exception
capture point today is FastAPI's `@app.exception_handler(Exception)` in
`engine/src/atlas_engine/api.py` (lines 80-95) — it covers HTTP route
exceptions only. Three gaps are currently invisible to anyone:

- The background thread running `uvicorn.run(...)` (`desktop/app.py`).
- The main thread running `webview.start()` (`desktop/app.py`).
- The CLI entry point (`engine/src/atlas_engine/cli.py`).

Logging exists (`engine/src/atlas_engine/observability.py`: stdlib JSON
logging, optional rotating file at `~/.atlas/logs/atlas-desktop.log` when
`ATLAS_LOG_FILE` is set) but nothing alerts in real time, and the windowed/
frozen PyInstaller build has no visible console — a crash outside a FastAPI
route is silent to the user and to us.

## Scope

- Desktop exe only. The backend/Container Apps deployment already has
  stdout → Log Analytics and is explicitly out of scope (see
  `2026-08-17-backend-production-deployment-design.md`, which deferred
  APM/OpenTelemetry for that deployment on the grounds the log pipeline was
  sufficient — that reasoning doesn't hold for the standalone exe, so this
  spec does not change the backend).
- Error tracking only — no APM/performance tracing/dashboards.
- Sentry cloud SaaS (free tier). No self-hosted infra.

## Architecture

New `init_error_tracking()` function in
`engine/src/atlas_engine/observability.py`, alongside the existing
`configure_logging()`. Reads `ATLAS_SENTRY_DSN` directly from `os.environ`
(same pattern as `ATLAS_LOG_FILE` is read today, independent of the
`Settings`/pydantic-settings mechanism) — if unset, it's a no-op, so local
dev and CI test runs are unaffected.

```python
def init_error_tracking() -> None:
    dsn = os.environ.get("ATLAS_SENTRY_DSN")
    if not dsn:
        return
    import sentry_sdk
    sentry_sdk.init(
        dsn=dsn,
        environment="desktop-exe",
        release=_app_version(),
        send_default_pii=False,
        include_local_variables=False,
    )
```

Called from `desktop/app.py`, right after `_configure_log_file()` and
before `atlas_engine.api` is imported — matching the existing ordering
where env vars must be set before any module that depends on them is
loaded.

## Capture points

- **Main thread** (`desktop/app.py`): wrap the body of `main()` in
  try/except; on exception call `sentry_sdk.capture_exception(e)` then
  re-raise, so startup/window failures (`_load_production_env`,
  `webview.start()`, etc.) are reported before the process exits.
- **Background uvicorn thread**: covered automatically by Sentry's default
  `ThreadingIntegration` (enabled by default in `sentry_sdk.init`) — no
  extra code needed.
- **FastAPI routes**: add `sentry_sdk.capture_exception(exc)` inside the
  existing `unhandled_exception_handler` (`api.py:80-95`), before building
  the generic 500 response. Tag the Sentry scope with `request_id` so a
  reported error can be cross-referenced with the local JSON log line.
- **CLI** (`cli.py`): Sentry's default excepthook integration (installed by
  `sentry_sdk.init`) catches uncaught exceptions in the main thread
  automatically, so no separate wrapping is needed here beyond calling
  `init_error_tracking()` early in the CLI's own startup path if/when the
  CLI is run standalone (it currently isn't wired into `ATLAS_SENTRY_DSN`
  packaging since only the exe bakes `production.env`; this is fine since
  the CLI's primary use is local dev, not the shipped product).
- **Breadcrumbs**: Sentry's default `LoggingIntegration` automatically
  attaches recent `logger.*` calls as breadcrumbs on any captured error —
  no extra code, works with the existing stdlib logging setup.

## Config

- Add `sentry_dsn: str | None = None` to `Settings`
  (`engine/src/atlas_engine/config.py`), for consistency with how other
  config is documented, even though the desktop entry point reads the raw
  env var before `Settings` is constructed (same split already used for
  Azure OpenAI keys).
- CI (`.github/workflows/build-exe.yml`): add a new GitHub secret
  `SENTRY_DSN`, written into `desktop/production.env` as
  `ATLAS_SENTRY_DSN=...`, alongside the existing `ATLAS_DATABASE_URL` /
  `ATLAS_SESSION_SECRET` / `ATLAS_AZURE_STORAGE_CONNECTION_STRING` secrets.
- New dependency: `sentry-sdk` added to `engine/pyproject.toml`.

## Secret safety

The exe bakes in production DB credentials and the session secret as env
vars, so default Sentry stack-trace behavior must not leak them:

- `include_local_variables=False` — stack trace frames show file/line/
  function only, never variable values.
- `send_default_pii=False` — keeps Sentry's own PII scrubbing enabled.

No `before_send` scrubbing hook is needed as long as local variables are
never captured; if a future change needs `include_local_variables=True`
for better debugging, a `before_send` denylist (`session_secret`,
`azure_storage_connection_string`, `database_url`) must be added at the
same time.

## Testing

- Unit test (`engine/tests/`): mock `sentry_sdk.init`, assert
  `init_error_tracking()` calls it with the expected kwargs when
  `ATLAS_SENTRY_DSN` is set, and asserts it is *not* called when the env
  var is absent.
- Manual, pre-release: add a temporary debug trigger (e.g. a hidden CLI
  flag or route) that raises a test exception in a built exe with a real
  DSN, confirm the event appears in the Sentry dashboard with correct
  `environment`/`release` tags and no leaked secret values, then remove
  the trigger.

## Non-goals

- No APM/performance tracing, no dashboards beyond Sentry's default error
  view.
- No changes to the backend/Container Apps deployment's logging pipeline.
- No self-hosted Sentry/GlitchTip infra.
