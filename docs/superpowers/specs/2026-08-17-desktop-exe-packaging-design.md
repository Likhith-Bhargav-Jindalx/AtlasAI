# Desktop Exe Packaging — Design Note

**Status:** Self-contained local-mode exe built and verified (macOS + a real Windows CI build); remote-submit mode (exe → shared deployed backend over HTTPS) now built and verified against a simulated remote backend — real Azure deployment still pending, see sub-project 1
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

## Known limitations, deliberately deferred

1. **Not tested against a real Azure deployment.** The remote-submit mechanism is proven against a simulated remote (a second local instance) — sub-project 1's actual cloud deployment still needs to exist before this can be tested for real, and before any agent's exe should point `ATLAS_REMOTE_BACKEND_URL` at it.
2. **Multi-tab recordings aren't supported through `submit-capture` yet.** `additional_pages` (extra tabs/popups opened during capture) is accepted by `persist_recording` and `submit_capture_to_remote`'s signature for compatibility, but the remote endpoint doesn't have fields for it — logged as a warning, not silently dropped. Most recordings are single-page; revisit if multi-tab recording in remote-submit mode turns out to matter in practice.
3. **The proxy's route list needs manual upkeep.** See point 4 in the architecture section above — a real, accepted trade-off for not having to guess which arbitrary paths are API calls vs. static assets.
4. **SPA deep-link refresh isn't handled.** `StaticFiles(html=True)` only falls back to `index.html` at the root; a manual browser-refresh on a client-side sub-route would 404. Low priority — pywebview always starts at `/`, and in-app navigation is client-side (no full page reloads).
5. **Distribution is a ~700MB zip**, dominated by the bundled Chromium build. Acceptable for handing to internal ops agents over a file share; an installer (as the original architecture spec's roadmap eventually calls for) would be a later polish pass, not required for this to work.

## Files

- `desktop/app.py` — the entrypoint; sets `PLAYWRIGHT_BROWSERS_PATH=0` before any Playwright import; chooses local vs. remote-submit mode based on `ATLAS_REMOTE_BACKEND_URL`
- `desktop/remote_app.py` — the remote-submit mode's local server (capture endpoints + reverse proxy)
- `desktop/build.sh` — the full build: frontend build, vendor Chromium, PyInstaller, copy the browser back into the bundle, zip for distribution (macOS/Linux; Windows CI has its own equivalent steps in `build-exe.yml`, since venv layout differs)
- `desktop/requirements-desktop.txt` — `pywebview`, `pyinstaller`
- `desktop/frontend_dist` — dev-mode symlink to `../frontend/dist`, for running `desktop/app.py` unfrozen without a PyInstaller build
- `.github/workflows/build-exe.yml` — Windows exe build workflow (verified on a real `windows-latest` runner)
- `engine/src/atlas_engine/remote_submit.py` — the HTTP-submission client function
- `engine/src/atlas_engine/api.py`'s `/recordings/submit-capture` — the server-side receiving endpoint
