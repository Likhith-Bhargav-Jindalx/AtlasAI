# Desktop Exe Packaging — Design Note

**Status:** Built and verified on macOS; Windows exe pending a CI run
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

## Known limitations, deliberately deferred

1. **Chromium is not bundled or auto-installed.** The app assumes `playwright install chromium` has already been run on the machine executing it (true on this dev machine, from earlier work in this session). Bundling the browser inside the exe, or auto-installing on first run, is real additional complexity (PyInstaller-frozen executables can't shell out to `python -m playwright install` the normal way — `sys.executable` inside a frozen app is the app itself, not a python interpreter) and is out of scope for this iteration, which targets testing on this machine, not distributing to other agents' machines yet.
2. **Local backend only.** No cloud Postgres/Blob wiring yet — see sub-project 1.
3. **SPA deep-link refresh isn't handled.** `StaticFiles(html=True)` only falls back to `index.html` at the root; a manual browser-refresh on a client-side sub-route would 404. Low priority — pywebview always starts at `/`, and in-app navigation is client-side (no full page reloads).
4. **The real Windows `.exe` isn't built yet.** PyInstaller doesn't cross-compile — building on this Mac produces a macOS app bundle, useful for validating the packaging mechanics, not the deliverable. `.github/workflows/build-exe.yml` (a `windows-latest` runner, `workflow_dispatch` + push-triggered) builds the actual Windows exe as a downloadable artifact — this hasn't run yet (needs a push/manual trigger) and its exact PyInstaller invocation (Windows uses `;` instead of `:` as the `--add-data` separator, already accounted for) hasn't been verified against a real Windows runner.

## Files

- `desktop/app.py` — the entrypoint
- `desktop/requirements-desktop.txt` — `pywebview`, `pyinstaller`
- `desktop/frontend_dist` — dev-mode symlink to `../frontend/dist`, for running `desktop/app.py` unfrozen without a PyInstaller build
- `.github/workflows/build-exe.yml` — Windows exe build workflow
