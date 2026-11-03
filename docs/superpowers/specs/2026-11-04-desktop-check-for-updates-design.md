# Desktop Check-for-Updates — Design

**Date:** 2026-11-04
**Author:** Siddharth Singh (Anka Health), designed with Claude
**Status:** Proposed

## Problem

Every desktop release today is manual end to end: build locally with `desktop/build.sh`, zip it, upload to OneDrive, tell whoever needs it to re-download and reinstall. There is no version number anywhere in the app (`desktop/VERSION` doesn't exist; `engine/pyproject.toml`'s `version = "0.1.0"` is unrelated), so nobody — including the app itself — can tell two builds apart.

Separately, `.github/workflows/build-exe.yml` already builds the Windows exe on every push to `main` and uploads it to a GitHub Release — but the release is a single floating tag (`latest-windows`), clobbered on every run, so it's a "give me whatever's newest" link, not a real version history, and nothing in the app checks it. All current users are on Windows, so this design covers Windows only; mac is out of scope.

## Goal

The app checks GitHub Releases for a newer version on launch, shows a banner if one exists, and updating is a single click inside the app — no manual zip download, no manual file merging.

## Why GitHub Releases, not Azure Blob

The repo is public, so release assets are downloadable over plain HTTPS with no auth and no egress cost. `build-exe.yml` already produces a Windows build and publishes it to a release on every push to `main`. Standing up an Azure Blob container, a manifest.json, and a publish script would duplicate infrastructure that already exists for free. The only real gaps are: no real version tags, one monolithic zip per build, and no client-side check.

## Architecture

### 1. Versioning

A new `desktop/VERSION` file (plain text, e.g. `1.2.0`), bumped by hand before a release, baked into the PyInstaller bundle the same way `desktop/production.env` already is (`--add-data`). Read at runtime via the existing `_resource_path()` helper in `desktop/app.py`.

### 2. CI publishing (`build-exe.yml`)

The existing Windows job gains:

- **Version gate**: before publishing, check whether a GitHub release tagged `desktop-v<VERSION>` already exists (`gh release view`). If it does, the commit's build still runs (regression check, same as today) but nothing new is published — most commits touching `desktop/**`/`engine/src/**`/`frontend/src/**` don't bump `desktop/VERSION`, and shouldn't spam a new release each time.
- **Split zips**, built from the same `dist/atlas-desktop` output as today:
  - `atlas-desktop-app-<version>.zip` — the exe plus everything in `_internal` *except* `playwright/`.
  - `atlas-desktop-vendor-<playwright_version>.zip` — just `_internal/playwright` (the vendored Chromium; ~823MB of the current ~889MB bundle, measured against a real build in `~/Downloads/atlas-desktop-windows_new`). `<playwright_version>` comes from `pip show playwright` in the build venv.
  - `checksums.txt` — sha256 of both zips.
- **Publish**: `gh release create desktop-v<version>` with the release body set to the version and a short changelog note, all three files uploaded as assets. Both zips are uploaded every release regardless of whether the vendor one changed — storage/bandwidth on a public repo's releases is free, so there's no "skip re-upload" logic to maintain. The old `latest-windows` floating release is left alone (or retired separately; not load-bearing for this design).

This replaces the old plan of a hand-run `publish_release.sh` — publishing is just "bump `desktop/VERSION`, push to `main`."

### 3. Client update check

New route in `desktop/app.py`'s FastAPI app: `GET /update/check`.

- Reads local `desktop/VERSION` and local `importlib.metadata.version("playwright")` (works against the frozen bundle's own installed metadata, no new baked file needed).
- `GET https://api.github.com/repos/Likhith-Bhargav-Jindalx/AtlasAI/releases/latest` — unauthenticated, public repo, short timeout (e.g. 3s).
- Any failure (offline, non-200, malformed JSON) → `{"update_available": false}`, logged at `warning`, never raised. This must never block app usage.
- On success: parse `tag_name` (strip `desktop-v` prefix) as the latest version, semver-compare against local `desktop/VERSION`. Find the app/vendor/checksums assets by filename convention; parse the vendor asset's filename for its playwright version, compare (exact string match, not semver) against the local one.
- Response: `{update_available, latest_version, notes, app_asset_url, app_sha256, vendor_update_required, vendor_asset_url, vendor_sha256}`.

Frontend: a small `UpdateBanner` component calls `/update/check` a few seconds after the app loads (non-blocking). If `update_available`, shows a dismissible banner with `notes` and an "Update now" button. No background polling beyond the one on-launch check — a user who dismisses it can relaunch later to see it again (no "check for updates" menu item requested; can be added later without touching the backend route).

### 4. Self-update (single click)

"Update now" calls `POST /update/apply`:

1. Downloads `app_asset_url` (and `vendor_asset_url`, only if `vendor_update_required`) into a staging folder under the OS temp dir. Verifies each against its sha256 from the check response. Any failure here aborts and leaves the current install untouched — nothing is swapped until both downloads are verified.
2. Writes a PowerShell swap script to temp, parameterized with the current process's PID and the staging/install paths (install path is `Path(sys.executable).parent` — the app locates itself at runtime, so this works regardless of where the user unzipped it originally; no fixed install location is assumed).
3. Launches that script detached (`subprocess.Popen`, no window), then the app exits — releasing the file locks Windows holds on a running exe/DLL.
4. The script: waits for the given PID to disappear, then `robocopy staged install /E` — a **plain copy, not a mirror**. This only touches files present in `staged`; when the vendor zip wasn't staged, `install`'s existing `playwright/` subfolder is never touched (robocopy without `/MIR` never deletes destination-only files). Relaunches `atlas-desktop.exe` from `install`, then deletes the staging folder.

### Error handling

- `/update/check` never raises; worst case is simply no banner.
- `/update/apply`'s download/verify step aborts cleanly on any failure (bad hash, network error, disk full) before anything touches the real install — the swap only ever starts once both files are confirmed good on disk.
- The swap script itself has no transactional rollback: if `robocopy` is interrupted mid-copy (power loss, forced kill), the install can be left inconsistent. Deliberately not solved here — see Known Limitations.

### Testing

- Unit tests for the version-compare logic (`1.2.3` vs `1.3.0`, equal versions, malformed strings) and for parsing the version out of the vendor asset's filename convention.
- No automated test hits the live GitHub API or exercises a real end-to-end swap — both are exercised manually, once per release of this feature, before shipping it.

## Known limitations, deliberately deferred

1. **No atomic rollback on a failed swap.** Acceptable because install directories are always user-writable locations (Desktop/Downloads, never `Program Files`), so the operations that could fail (disk full, abrupt kill) are rare, and the worst case — re-download and unzip manually — is exactly what the user does today, every time.
2. **Mac gets none of this.** No CI job, no update check, no self-update. Revisit if/when mac users exist; the client-side design (self-locating via the running binary's own path, verified-download-before-swap) generalizes to mac, but the swap mechanics (`.app` bundle replacement) would need their own script, not the Windows `.ps1`.
3. **No "check for updates" menu item** — only the automatic on-launch check. Add later if a user wants to re-check without relaunching.
4. **No signed/notarized installer.** This stays a `--onedir` PyInstaller bundle; integrity is a sha256 check against the GitHub release, not code signing. Matches the project's current ad-hoc-signing posture (see `docs/superpowers/specs/2026-08-17-desktop-exe-packaging-design.md`), not a regression from it.

## Files

- `desktop/VERSION` — new, the app's own version string, baked into the bundle
- `desktop/app.py` — new `/update/check` and `/update/apply` routes; reads `desktop/VERSION` and the playwright package metadata
- `desktop/updater.ps1` (or generated inline) — the detached swap script
- `.github/workflows/build-exe.yml` — version-gated release publishing, split app/vendor zips, checksums
- `frontend/src/components/` — new `UpdateBanner` component
