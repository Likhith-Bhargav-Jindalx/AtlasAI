# Desktop Exe Packaging — Design Note

**Status:** SUPERSEDED (2026-08-17, later same day). The remote-submit mode described below was ripped out — the exe now always connects directly to production Postgres and Blob Storage with credentials baked into the build (see desktop/production.env.example); there is no deployed backend at all. Kept for historical context on how local-mode packaging (Chromium bundling, pywebview, PyInstaller) works, since that part is unchanged.
**Date:** 2026-08-17
**Author:** Likhith Bhargav (Anka Health), designed with Claude

Approved in chat rather than through a full brainstorming cycle, given how much context the earlier backend-hardening sub-project (`docs/superpowers/specs/2026-08-17-backend-production-deployment-design.md`) and the original architecture spec (`docs/superpowers/specs/2026-08-12-atlas-ai-production-architecture-design.md`, §9) already established. This note documents what was actually built, for the record.

## What this is

Today's local-dev app — the `engine/` FastAPI app (auth, recording, authoring, healing) plus Playwright capture, and the React frontend — packaged into one launchable desktop app. Two modes:

- **Local mode** (`ATLAS_REMOTE_BACKEND_URL` unset): targets local Postgres/storage exactly like `npm run dev` + `uvicorn` do today.
- **Remote-submit mode** (`ATLAS_REMOTE_BACKEND_URL` set): the exe still drives browser capture locally — that part is inherently tied to the agent's own machine and session — but submits finished recordings to a deployed backend over HTTPS and proxies every other API call there too, instead of writing to a database/blob store directly. This is what makes the multi-agent rollout possible without opening a shared production database to every agent's desktop machine.

## Architecture

`desktop/app.py` is the entrypoint: it imports the existing `atlas_engine.api:app` unchanged, mounts the frontend's production build (`frontend/dist`) as static files at `/` (mounted *after* the API's own routes are registered, so `/health`, `/auth/login`, etc. still win — the mount only catches what no API route matched), starts `uvicorn` on a background thread, and opens a `pywebview` native window pointed at `http://127.0.0.1:8000/`. One frontend codebase, shown in a native window instead of a browser tab, exactly per the original architecture spec's §9 intent.

Packaged with PyInstaller (`--onedir --windowed`, `--add-data` to bundle `frontend/dist`) — this built and ran successfully on the first real attempt, largely because Playwright ships its own official PyInstaller hook (`playwright/_impl/__pyinstaller`), which resolved what was expected to be the hardest part.

## Verified working (this iteration, on macOS)

- Unfrozen (`python desktop/app.py`): a pywebview window opened, requested and rendered the frontend's JS/CSS/fonts, `/health` returned `{"status":"ok"}`.
- Frozen (PyInstaller `--onedir` build, run directly): same result — `/health` responds, `/` serves the built `index.html` and assets, and the process stayed alive blocked inside `webview.start()` (strong evidence the window opened; this sandbox has no way to screenshot/verify the window visually).
- **Self-containment, end-to-end, with the machine's global Playwright cache renamed out of the way**: started a real live-recording session through the frozen exe's `/recordings/start` endpoint (the actual production code path, not a synthetic test) and confirmed via `ps` that the launched Chromium process ran from *inside* the frozen bundle (`dist/atlas-desktop/_internal/playwright/driver/package/.local-browsers/...`), stopped the recording, and got back a real `recording_id` — the full capture → persist pipeline works with zero browser dependency on the host machine.

## Chromium bundling — how it's done

`PLAYWRIGHT_BROWSERS_PATH=0` (set at the top of `desktop/app.py`, before any Playwright import) tells Playwright to look for its browser inside its own package installation instead of the OS-level cache (`~/.cache/ms-playwright` or platform equivalent). Playwright's official PyInstaller hook (`playwright/_impl/__pyinstaller`) already bundles the `playwright` package's data files via `collect_data_files`, which would pick up a vendored browser automatically — but PyInstaller's binary-processing/codesigning step can't handle the nested macOS `.app` bundle structure inside a vendored Chromium build if it's present *during* the build (`SystemError: Failed to process binary '...chrome-mac-arm64/Google Chrome for Testing.app/...'`). The fix, encoded in `desktop/build.sh`: install Chromium vendored (`PLAYWRIGHT_BROWSERS_PATH=0 playwright install chromium`), move the resulting `.local-browsers` directory aside, run the normal PyInstaller build without it present, then copy it into the frozen bundle's `playwright/driver/package/` directory as a plain post-build file copy — bypassing PyInstaller's binary pipeline entirely for those files.

## Remote-submit mode — architecture

Three pieces, all new:

1. **`atlas_engine.live_capture`** — `_LiveCaptureSession`/`start_live_capture` now take a `persist_fn` parameter (defaulting to `recording_service.persist_recording`, so local mode is byte-for-byte unchanged). This is the one seam that makes remote-submit possible without duplicating any of the actual browser-automation code: the capture itself doesn't know or care where the finished artifacts end up.
2. **`atlas_engine.api`'s new `POST /recordings/submit-capture`** — a multipart endpoint (needs `python-multipart`, now a real dependency, not dev-only) that accepts actions JSON, an optional video file, an optional codegen script, and optional trace/HAR artifacts, then calls `persist_recording` exactly as a local capture would — except `org_id`/`user_id` are **not** accepted as request fields at all; they come from the authenticated session (`get_current_user`), so a client can't submit into an org it doesn't belong to. Runs on whichever backend it's deployed to — no code difference between "local dev" and "the real Azure deployment" here.
3. **`atlas_engine.remote_submit.submit_capture_to_remote`** — same call signature as `persist_recording`, passed as `persist_fn` when running in remote-submit mode; POSTs to `{base_url}/recordings/submit-capture` with the session cookie instead of writing anywhere directly.
4. **`desktop/remote_app.py`** — built by `desktop/app.py` instead of importing `atlas_engine.api` directly when `ATLAS_REMOTE_BACKEND_URL` is set. Implements `/recordings/start` and `/recordings/{id}/stop` locally (driving Playwright, using `submit_capture_to_remote` as the persist step), and reverse-proxies every other route to the deployed backend — forwarding cookies/headers verbatim in both directions, which is what lets the same frontend build (always pointed at `localhost:8000`) work unchanged in either mode. The proxied paths are an explicit list mirroring `atlas_engine/api.py`'s route table, not a blanket catch-all, so a new top-level path added there later needs a matching line added in `remote_app.py`.

**Verified end-to-end** (no real Azure yet, so tested against a second local `atlas_engine.api` instance on a different port standing in for "the deployed backend"): logged in through the proxy, started a real headed-browser recording locally, stopped it, and confirmed via the *simulated remote's own* `/recordings` list and a direct DB query that the recording landed there — correct `org_id`/`user_id` (from the session, matching what a client could never spoof since there's nothing to spoof), a real persisted video (verified via a byte-for-byte download), and a real codegen script. The local instance never touched its own database at all in this mode.

## Remote-submit mode — authoring runs locally too

Recording capture is one thing, but authoring (replay → brain → golden → certify) has the *same* live-network requirement — it replays the trajectory against the real target portal, which a generic Azure VM likely can't even reach (VPN-gated internal/healthcare portals). So authoring also needs to run on the developer's own machine, not the deployed backend. Unlike recording, authoring writes to the database at many points throughout a multi-phase pipeline (`atlas_engine.authoring.run`'s `_AuthoringRunSession`), not once at the end — extending the single-`persist_fn`-seam approach wasn't enough here.

**The approach:** run `atlas_engine.authoring.*`'s existing, completely unmodified code against an ephemeral local SQLite database + local disk instead of the deployed backend's real Postgres/Blob — those modules only ever call `session_scope()`/`get_store()`, never anything Postgres- or Azure-specific, so this needed zero changes to the authoring pipeline itself. Then submit the final result once the run reaches a terminal state.

1. **`atlas_engine.scratch_env.activate_scratch_environment()`** — repoints `atlas_engine.db`/`config.settings` at a fresh SQLite file + temp directory, for the process's lifetime. Alembic's migrations use raw `ALTER`-constraint operations SQLite can't run, so this bypasses migrations entirely and calls `Base.metadata.create_all()` directly — correct for a throwaway database that's never migrated forward. Also registers a `before_insert` listener that manually assigns `AuthoringRunEvent.seq` (a Postgres `IDENTITY` column with no SQLite equivalent on a non-primary-key column — inserts fail with a `NOT NULL` violation without it), scoped so it never touches a real Postgres-backed row.
2. **`atlas_engine.authoring_export`** — `export_recording_for_authoring` (server-side: gathers org/portal-profile/trajectory data) and `import_recording_for_authoring` (client-side: recreates the minimal rows + blob files in the scratch database so the unmodified pipeline has a real `Recording` row to look up).
3. **New server endpoints**: `GET /recordings/{id}/export-for-authoring` and `POST /recordings/{id}/submit-authoring-result` (multipart: golden script text, certification run results + failure-evidence screenshots matched by filename, the full event log) — both require `job_function=developer`, called directly (not through the local proxy) since they're server-to-server.
4. **`desktop/local_authoring.py`** — orchestrates: fetch export → import into scratch → `start_authoring_run` (unmodified) → on each progress poll, `submit_if_terminal` checks the scratch database and, the first time the run is done, POSTs the golden script + certification runs + events to the deployed backend.
5. **`desktop/remote_app.py`** gained three more local (non-proxied) routes: `/recordings/{id}/author`, `/authoring-runs/{id}` (GET, polling — hand-built to the same response shape as `atlas_engine.api`'s own endpoint, reading the scratch database directly), and its `/resume`/`/stop`.

**Verified end-to-end** against the same simulated-remote setup: started local authoring on an already-recorded example.com session, polled through replay → golden → certifying phases for real, reached `status: "completed"` with a certified golden script (3/3 certification passes) — then confirmed via the *simulated remote's own* `/authoring-runs/{id}` endpoint that a brand-new `AuthoringRun` row (a different id than the scratch-local one) existed there with the identical golden script text, all 3 certification run records, and all 6 event log entries. The scratch database was the only thing that ever held this data during the run itself.

## Known limitations, deliberately deferred

1. **Not tested against a real Azure deployment.** Both remote-submit mechanisms (recording and authoring) are proven against a simulated remote (a second local instance) — sub-project 1's actual cloud deployment still needs to exist before this can be tested for real, and before any agent's or developer's exe should point `ATLAS_REMOTE_BACKEND_URL` at it.
2. **Multi-tab recordings aren't supported through `submit-capture` yet.** `additional_pages` (extra tabs/popups opened during capture) is accepted by `persist_recording` and `submit_capture_to_remote`'s signature for compatibility, but the remote endpoint doesn't have fields for it — logged as a warning, not silently dropped. Most recordings are single-page; revisit if multi-tab recording in remote-submit mode turns out to matter in practice.
3. **The proxy's route list needs manual upkeep.** See point 4 in the architecture section above — a real, accepted trade-off for not having to guess which arbitrary paths are API calls vs. static assets.
4. **LLM usage/cost tracking during local authoring stays local**, in the scratch database, and is discarded — never synced to the deployed backend's `usage_ledger`. Centralized cost tracking across developers would need this; not built.
5. **A developer closing the exe mid-authoring-run loses that run's state** — the scratch database and in-memory session registry are both process-lifetime-scoped. `atlas_engine.authoring.run.stop_authoring_run`'s existing orphan-reconciliation logic (for "the server restarted mid-run") applies here unchanged, but there's no cross-restart resume.
6. **SPA deep-link refresh isn't handled.** `StaticFiles(html=True)` only falls back to `index.html` at the root; a manual browser-refresh on a client-side sub-route would 404. Low priority — pywebview always starts at `/`, and in-app navigation is client-side (no full page reloads).
7. **Distribution is a ~700MB zip**, dominated by the bundled Chromium build. Acceptable for handing to internal ops agents over a file share; an installer (as the original architecture spec's roadmap eventually calls for) would be a later polish pass, not required for this to work.

## Files

- `desktop/app.py` — the entrypoint; sets `PLAYWRIGHT_BROWSERS_PATH=0` before any Playwright import; chooses local vs. remote-submit mode based on `ATLAS_REMOTE_BACKEND_URL`
- `desktop/remote_app.py` — the remote-submit mode's local server (capture + authoring endpoints, and the reverse proxy for everything else)
- `desktop/local_authoring.py` — runs authoring against the scratch environment and submits the result
- `desktop/build.sh` — the full build: frontend build, vendor Chromium, PyInstaller, copy the browser back into the bundle, zip for distribution (macOS/Linux; Windows CI has its own equivalent steps in `build-exe.yml`, since venv layout differs)
- `desktop/requirements-desktop.txt` — `pywebview`, `pyinstaller`
- `desktop/frontend_dist` — dev-mode symlink to `../frontend/dist`, for running `desktop/app.py` unfrozen without a PyInstaller build
- `.github/workflows/build-exe.yml` — Windows exe build workflow (verified on a real `windows-latest` runner)
- `engine/src/atlas_engine/remote_submit.py` — the recording-submission HTTP client function
- `engine/src/atlas_engine/scratch_env.py` — the ephemeral local SQLite database/storage environment
- `engine/src/atlas_engine/authoring_export.py` — the export/import bridge for authoring
- `engine/src/atlas_engine/api.py`'s `/recordings/submit-capture`, `/recordings/{id}/export-for-authoring`, `/recordings/{id}/submit-authoring-result` — the server-side endpoints
