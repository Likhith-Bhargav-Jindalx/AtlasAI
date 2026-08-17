# Desktop Exe Packaging — Design Note

**Status:** Built, self-contained, and verified end-to-end on macOS (including a real browser launch through the frozen exe with no Chromium installed anywhere on the machine); Windows exe still pending an actual CI run (no GitHub remote configured yet)
**Date:** 2026-08-17
**Author:** Likhith Bhargav (Anka Health), designed with Claude

Approved in chat rather than through a full brainstorming cycle, given how much context the earlier backend-hardening sub-project (`docs/superpowers/specs/2026-08-17-backend-production-deployment-design.md`) and the original architecture spec (`docs/superpowers/specs/2026-08-12-atlas-ai-production-architecture-design.md`, §9) already established. This note documents what was actually built, for the record.

## What this is

Today's local-dev app — the `engine/` FastAPI app (auth, recording, authoring, healing) plus Playwright capture, and the React frontend — packaged into one launchable desktop app, targeting local Postgres/storage exactly like `npm run dev` + `uvicorn` do today. Wiring it to a shared cloud backend instead (once sub-project 1's real Azure deployment exists) is a config change, not more packaging work.

## Architecture

`desktop/app.py` is the entrypoint: it imports the existing `atlas_engine.api:app` unchanged, mounts the frontend's production build (`frontend/dist`) as static files at `/` (mounted *after* the API's own routes are registered, so `/health`, `/auth/login`, etc. still win — the mount only catches what no API route matched), starts `uvicorn` on a background thread, and opens a `pywebview` native window pointed at `http://127.0.0.1:8000/`. One frontend codebase, shown in a native window instead of a browser tab, exactly per the original architecture spec's §9 intent.

Packaged with PyInstaller (`--onedir --windowed`, `--add-data` to bundle `frontend/dist`) — this built and ran successfully on the first real attempt, largely because Playwright ships its own official PyInstaller hook (`playwright/_impl/__pyinstaller`), which resolved what was expected to be the hardest part.

## Verified working (this iteration, on macOS)

- Unfrozen (`python desktop/app.py`): a pywebview window opened, requested and rendered the frontend's JS/CSS/fonts, `/health` returned `{"status":"ok"}`.
- Frozen (PyInstaller `--onedir` build, run directly): same result — `/health` responds, `/` serves the built `index.html` and assets, and the process stayed alive blocked inside `webview.start()` (strong evidence the window opened; this sandbox has no way to screenshot/verify the window visually).
- **Self-containment, end-to-end, with the machine's global Playwright cache renamed out of the way**: started a real live-recording session through the frozen exe's `/recordings/start` endpoint (the actual production code path, not a synthetic test) and confirmed via `ps` that the launched Chromium process ran from *inside* the frozen bundle (`dist/atlas-desktop/_internal/playwright/driver/package/.local-browsers/...`), stopped the recording, and got back a real `recording_id` — the full capture → persist pipeline works with zero browser dependency on the host machine.

## Chromium bundling — how it's done

`PLAYWRIGHT_BROWSERS_PATH=0` (set at the top of `desktop/app.py`, before any Playwright import) tells Playwright to look for its browser inside its own package installation instead of the OS-level cache (`~/.cache/ms-playwright` or platform equivalent). Playwright's official PyInstaller hook (`playwright/_impl/__pyinstaller`) already bundles the `playwright` package's data files via `collect_data_files`, which would pick up a vendored browser automatically — but PyInstaller's binary-processing/codesigning step can't handle the nested macOS `.app` bundle structure inside a vendored Chromium build if it's present *during* the build (`SystemError: Failed to process binary '...chrome-mac-arm64/Google Chrome for Testing.app/...'`). The fix, encoded in `desktop/build.sh`: install Chromium vendored (`PLAYWRIGHT_BROWSERS_PATH=0 playwright install chromium`), move the resulting `.local-browsers` directory aside, run the normal PyInstaller build without it present, then copy it into the frozen bundle's `playwright/driver/package/` directory as a plain post-build file copy — bypassing PyInstaller's binary pipeline entirely for those files.

## Known limitations, deliberately deferred

1. **Local backend only.** No cloud Postgres/Blob wiring yet — see sub-project 1.
2. **SPA deep-link refresh isn't handled.** `StaticFiles(html=True)` only falls back to `index.html` at the root; a manual browser-refresh on a client-side sub-route would 404. Low priority — pywebview always starts at `/`, and in-app navigation is client-side (no full page reloads).
3. **The real Windows `.exe` still hasn't actually been built by CI.** PyInstaller doesn't cross-compile — building on this Mac produces a macOS app bundle, useful for validating the packaging mechanics (including the Chromium-bundling fix above, which is platform-independent in principle), not the deliverable. `.github/workflows/build-exe.yml` mirrors the same browser-vendoring steps for a `windows-latest` runner, but **the repo has no GitHub remote configured**, so no workflow — this one or any of sub-project 1's — has ever actually run. Producing a real, verified Windows exe needs either a GitHub remote to push to (so the workflow can run) or a real Windows machine to run `desktop/build.sh`'s Windows equivalent directly.
4. **Distribution is a ~700MB zip**, dominated by the bundled Chromium build. Acceptable for handing to internal ops agents over a file share; an installer (as the original architecture spec's roadmap eventually calls for) would be a later polish pass, not required for this to work.

## Files

- `desktop/app.py` — the entrypoint; sets `PLAYWRIGHT_BROWSERS_PATH=0` before any Playwright import
- `desktop/build.sh` — the full build: frontend build, vendor Chromium, PyInstaller, copy the browser back into the bundle, zip for distribution (macOS/Linux; Windows CI has its own equivalent steps in `build-exe.yml`, since venv layout differs)
- `desktop/requirements-desktop.txt` — `pywebview`, `pyinstaller`
- `desktop/frontend_dist` — dev-mode symlink to `../frontend/dist`, for running `desktop/app.py` unfrozen without a PyInstaller build
- `.github/workflows/build-exe.yml` — Windows exe build workflow (untested against a real runner — no GitHub remote yet)
