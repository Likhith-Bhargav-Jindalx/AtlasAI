# Desktop Check-for-Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The packaged Windows desktop app checks GitHub Releases for a newer version on launch and updates itself in place with a single click — no manual zip download, no manual file merging, no more OneDrive.

**Architecture:** CI (`build-exe.yml`) publishes a versioned GitHub Release with two zip assets per build — a small `app` zip (everything except the vendored Chromium) and a `vendor` zip (just the Chromium payload, ~823MB of the current ~889MB bundle) — plus a `checksums.txt`. The running app's `/update/check` route polls the public Releases API and compares versions; `/update/apply` downloads and verifies the (small, usually just the app) zip, writes a detached PowerShell script that waits for the app to exit, copies the new files over the install directory, and relaunches it.

**Tech Stack:** Python (FastAPI, httpx — both already dependencies), PowerShell (the swap script), React + `@tanstack/react-query` (frontend), GitHub Actions + `gh` CLI (already used in `build-exe.yml`).

## Global Constraints

- Windows only — no mac build, no mac update logic. (Spec: "Goal", "Why GitHub Releases, not Azure Blob".)
- No new infrastructure — GitHub Releases only, no Azure Blob, no manifest.json, no `publish_release.sh`.
- `/update/check` must never raise — any failure (offline, bad JSON, timeout) returns `{"update_available": false}`, logged at `warning`. Must never block normal app usage.
- `/update/apply` must verify sha256 of every downloaded asset before touching the real install; on any mismatch, abort before anything is swapped.
- The swap must never touch `_internal/playwright` unless a new vendor zip was actually staged (plain `robocopy`, not `/MIR`).
- No atomic rollback of a failed swap — documented, deliberate (see spec's Known Limitations).

---

## File Structure

- `engine/src/atlas_engine/desktop_update.py` — **new.** All testable update logic: version/filename/checksum parsing, the GitHub Releases API client, download+verify+stage, swap-script generation, launching the swap. Lives in the engine package (not under `desktop/`) so it runs through the existing `engine/tests` pytest suite in CI, matching how other desktop-adjacent logic (`scratch_env.py`, `authoring_export.py`, `remote_submit.py`) already lives in `atlas_engine`, not `desktop/`.
- `engine/tests/test_desktop_update.py` — **new.** Unit tests for everything in the module above except the two genuinely untestable-without-a-real-OS-process pieces (actually launching the swap process, actually exiting the app), which are smoke-tested manually once before shipping (per the spec).
- `desktop/VERSION` — **new.** Plain text, e.g. `1.0.0`. Baked into the PyInstaller bundle the same way `desktop/production.env` already is.
- `desktop/app.py` — **modify.** Two new routes (`GET /update/check`, `POST /update/apply`) registered on `fastapi_app` at module level (so they're present before the static-files mount in `main()`, same ordering requirement as every other API route).
- `.github/workflows/build-exe.yml` — **modify.** Replace the current "always clobber `latest-windows`" tail with: read `desktop/VERSION`, skip publishing if that version is already released, split the build into app/vendor zips + checksums, publish a real `desktop-v<version>` release.
- `frontend/src/lib/update.ts` — **new.** `useUpdateCheck()` / `useApplyUpdate()` hooks, following the `useQuery`/`useMutation` + `apiFetch` pattern already used throughout `frontend/src/lib/*.ts`.
- `frontend/src/lib/update.test.tsx` — **new.**
- `frontend/src/components/UpdateBanner.tsx` — **new.** Dismissible banner, shown when an update is available.
- `frontend/src/components/UpdateBanner.test.tsx` — **new.**
- `frontend/src/layout/AppLayout.tsx` — **modify.** Mount `<UpdateBanner />` above `<Outlet />`.

---

### Task 1: Version, filename, and checksum parsing

**Files:**
- Create: `engine/src/atlas_engine/desktop_update.py`
- Test: `engine/tests/test_desktop_update.py`

**Interfaces:**
- Produces: `parse_version(version: str) -> tuple[int, int, int]` (raises `ValueError` if not exactly 3 dot-separated integers, after stripping a leading `v`); `is_newer(remote_version: str, local_version: str) -> bool` (returns `False`, never raises, if either string fails to parse); `parse_vendor_version_from_filename(filename: str) -> str | None` (expects `atlas-desktop-vendor-<version>.zip`, returns `None` if the prefix doesn't match); `parse_checksums(text: str) -> dict[str, str]` (parses `sha256sum`-style lines: `"<hex>  <filename>"` per line, mapping filename to hex digest).

- [ ] **Step 1: Write the failing tests**

```python
# engine/tests/test_desktop_update.py
import pytest

from atlas_engine.desktop_update import (
    is_newer,
    parse_checksums,
    parse_vendor_version_from_filename,
    parse_version,
)


def test_parse_version_accepts_plain_semver():
    assert parse_version("1.2.3") == (1, 2, 3)


def test_parse_version_strips_leading_v():
    assert parse_version("v1.2.3") == (1, 2, 3)


def test_parse_version_rejects_malformed_strings():
    with pytest.raises(ValueError):
        parse_version("not-a-version")
    with pytest.raises(ValueError):
        parse_version("1.2")


def test_is_newer_true_when_remote_greater():
    assert is_newer("1.3.0", "1.2.9") is True


def test_is_newer_false_when_equal_or_older():
    assert is_newer("1.2.3", "1.2.3") is False
    assert is_newer("1.2.0", "1.3.0") is False


def test_is_newer_false_instead_of_raising_on_malformed_input():
    assert is_newer("garbage", "1.2.3") is False
    assert is_newer("1.2.3", "garbage") is False


def test_parse_vendor_version_from_filename():
    assert parse_vendor_version_from_filename("atlas-desktop-vendor-1.49.0.zip") == "1.49.0"


def test_parse_vendor_version_from_filename_returns_none_for_non_vendor_names():
    assert parse_vendor_version_from_filename("atlas-desktop-app-1.0.0.zip") is None


def test_parse_checksums():
    text = (
        "abc123  atlas-desktop-app-1.0.0.zip\n"
        "def456  atlas-desktop-vendor-1.49.0.zip\n"
    )
    assert parse_checksums(text) == {
        "atlas-desktop-app-1.0.0.zip": "abc123",
        "atlas-desktop-vendor-1.49.0.zip": "def456",
    }


def test_parse_checksums_ignores_blank_lines():
    assert parse_checksums("\nabc123  one.zip\n\n") == {"one.zip": "abc123"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_desktop_update.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atlas_engine.desktop_update'`

- [ ] **Step 3: Write the implementation**

```python
# engine/src/atlas_engine/desktop_update.py
"""Checks GitHub Releases for a newer desktop build and applies it in
place. Only used by the packaged Windows desktop exe (desktop/app.py) —
not part of the web-hosted deployment's API surface, and not imported
by atlas_engine.api.
"""

from __future__ import annotations


def parse_version(version: str) -> tuple[int, int, int]:
    parts = version.strip().lstrip("v").split(".")
    if len(parts) != 3:
        raise ValueError(f"not a version string: {version!r}")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def is_newer(remote_version: str, local_version: str) -> bool:
    try:
        return parse_version(remote_version) > parse_version(local_version)
    except ValueError:
        return False


def parse_vendor_version_from_filename(filename: str) -> str | None:
    prefix = "atlas-desktop-vendor-"
    if not filename.startswith(prefix) or not filename.endswith(".zip"):
        return None
    version = filename[len(prefix):-len(".zip")]
    return version or None


def parse_checksums(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, filename = parts
        result[filename.strip()] = digest.strip()
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_desktop_update.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/desktop_update.py engine/tests/test_desktop_update.py
git commit -m "feat: add version/filename/checksum parsing for desktop update checks"
```

---

### Task 2: GitHub Releases check

**Files:**
- Modify: `engine/src/atlas_engine/desktop_update.py`
- Test: `engine/tests/test_desktop_update.py`

**Interfaces:**
- Consumes: `is_newer`, `parse_vendor_version_from_filename`, `parse_checksums` from Task 1.
- Produces: `@dataclass UpdateCheckResult` with fields `update_available: bool`, `latest_version: str | None = None`, `notes: str | None = None`, `app_asset_url: str | None = None`, `app_sha256: str | None = None`, `vendor_update_required: bool = False`, `vendor_asset_url: str | None = None`, `vendor_sha256: str | None = None`. `check_for_update(local_version: str, local_vendor_version: str) -> UpdateCheckResult`. `GITHUB_RELEASES_LATEST_URL` module constant.

- [ ] **Step 1: Write the failing tests**

```python
# append to engine/tests/test_desktop_update.py
import httpx

from atlas_engine.desktop_update import UpdateCheckResult, check_for_update


class _FakeResponse:
    def __init__(self, json_data=None, text_data=""):
        self._json = json_data
        self.text = text_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def _release_payload(tag_name, app_name, vendor_name, body="release notes"):
    return {
        "tag_name": tag_name,
        "body": body,
        "assets": [
            {"name": app_name, "browser_download_url": f"https://example.test/{app_name}"},
            {"name": vendor_name, "browser_download_url": f"https://example.test/{vendor_name}"},
            {"name": "checksums.txt", "browser_download_url": "https://example.test/checksums.txt"},
        ],
    }


def test_check_for_update_reports_no_update_when_already_current(monkeypatch):
    payload = _release_payload("desktop-v1.0.0", "atlas-desktop-app-1.0.0.zip", "atlas-desktop-vendor-1.49.0.zip")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(json_data=payload))

    result = check_for_update(local_version="1.0.0", local_vendor_version="1.49.0")

    assert result == UpdateCheckResult(update_available=False)


def test_check_for_update_reports_available_update_without_vendor_change(monkeypatch):
    payload = _release_payload("desktop-v1.1.0", "atlas-desktop-app-1.1.0.zip", "atlas-desktop-vendor-1.49.0.zip")
    checksums_text = (
        "aaa111  atlas-desktop-app-1.1.0.zip\n"
        "bbb222  atlas-desktop-vendor-1.49.0.zip\n"
    )

    def fake_get(url, **kwargs):
        if url.endswith("checksums.txt"):
            return _FakeResponse(text_data=checksums_text)
        return _FakeResponse(json_data=payload)

    monkeypatch.setattr(httpx, "get", fake_get)

    result = check_for_update(local_version="1.0.0", local_vendor_version="1.49.0")

    assert result.update_available is True
    assert result.latest_version == "1.1.0"
    assert result.notes == "release notes"
    assert result.app_asset_url == "https://example.test/atlas-desktop-app-1.1.0.zip"
    assert result.app_sha256 == "aaa111"
    assert result.vendor_update_required is False
    assert result.vendor_asset_url is None
    assert result.vendor_sha256 is None


def test_check_for_update_requires_vendor_zip_when_vendor_version_changed(monkeypatch):
    payload = _release_payload("desktop-v1.1.0", "atlas-desktop-app-1.1.0.zip", "atlas-desktop-vendor-1.50.0.zip")
    checksums_text = (
        "aaa111  atlas-desktop-app-1.1.0.zip\n"
        "ccc333  atlas-desktop-vendor-1.50.0.zip\n"
    )

    def fake_get(url, **kwargs):
        if url.endswith("checksums.txt"):
            return _FakeResponse(text_data=checksums_text)
        return _FakeResponse(json_data=payload)

    monkeypatch.setattr(httpx, "get", fake_get)

    result = check_for_update(local_version="1.0.0", local_vendor_version="1.49.0")

    assert result.vendor_update_required is True
    assert result.vendor_asset_url == "https://example.test/atlas-desktop-vendor-1.50.0.zip"
    assert result.vendor_sha256 == "ccc333"


def test_check_for_update_never_raises_on_network_failure(monkeypatch):
    def fake_get(*a, **k):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "get", fake_get)

    result = check_for_update(local_version="1.0.0", local_vendor_version="1.49.0")

    assert result == UpdateCheckResult(update_available=False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_desktop_update.py -v -k check_for_update`
Expected: FAIL with `ImportError: cannot import name 'check_for_update'`

- [ ] **Step 3: Write the implementation**

```python
# add to engine/src/atlas_engine/desktop_update.py, below the parsing functions
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

GITHUB_RELEASES_LATEST_URL = (
    "https://api.github.com/repos/Likhith-Bhargav-Jindalx/AtlasAI/releases/latest"
)
_REQUEST_TIMEOUT_SECONDS = 3.0


@dataclass
class UpdateCheckResult:
    update_available: bool
    latest_version: str | None = None
    notes: str | None = None
    app_asset_url: str | None = None
    app_sha256: str | None = None
    vendor_update_required: bool = False
    vendor_asset_url: str | None = None
    vendor_sha256: str | None = None


def check_for_update(local_version: str, local_vendor_version: str) -> UpdateCheckResult:
    try:
        response = httpx.get(
            GITHUB_RELEASES_LATEST_URL,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            headers={"Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        payload = response.json()

        tag_name: str = payload["tag_name"]
        remote_version = tag_name.removeprefix("desktop-v")
        assets = {a["name"]: a["browser_download_url"] for a in payload["assets"]}

        app_asset_name = next((n for n in assets if n.startswith("atlas-desktop-app-")), None)
        vendor_asset_name = next((n for n in assets if n.startswith("atlas-desktop-vendor-")), None)
        checksums_url = assets.get("checksums.txt")

        if not app_asset_name or not checksums_url or not is_newer(remote_version, local_version):
            return UpdateCheckResult(update_available=False)

        checksums_response = httpx.get(checksums_url, timeout=_REQUEST_TIMEOUT_SECONDS)
        checksums_response.raise_for_status()
        checksums = parse_checksums(checksums_response.text)

        remote_vendor_version = (
            parse_vendor_version_from_filename(vendor_asset_name) if vendor_asset_name else None
        )
        vendor_update_required = bool(remote_vendor_version) and remote_vendor_version != local_vendor_version

        return UpdateCheckResult(
            update_available=True,
            latest_version=remote_version,
            notes=payload.get("body") or "",
            app_asset_url=assets[app_asset_name],
            app_sha256=checksums.get(app_asset_name),
            vendor_update_required=vendor_update_required,
            vendor_asset_url=assets.get(vendor_asset_name) if vendor_update_required else None,
            vendor_sha256=checksums.get(vendor_asset_name) if vendor_update_required else None,
        )
    except Exception:
        logger.warning("desktop update check failed", exc_info=True)
        return UpdateCheckResult(update_available=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_desktop_update.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/desktop_update.py engine/tests/test_desktop_update.py
git commit -m "feat: check GitHub Releases for a newer desktop build"
```

---

### Task 3: Stage, verify, and swap

**Files:**
- Modify: `engine/src/atlas_engine/desktop_update.py`
- Test: `engine/tests/test_desktop_update.py`

**Interfaces:**
- Consumes: `UpdateCheckResult` from Task 2.
- Produces: `class UpdateApplyError(Exception)`; `stage_update(result: UpdateCheckResult, staging_dir: Path) -> None` (downloads + verifies + extracts into `staging_dir`, raises `UpdateApplyError` and leaves `staging_dir` in an unusable state — caller is responsible for cleanup — on any checksum mismatch, never touches anything outside `staging_dir`); `write_swap_script(*, pid: int, staging_dir: Path, install_dir: Path, exe_name: str, script_path: Path) -> None`; `launch_swap_script(script_path: Path) -> subprocess.Popen`.

- [ ] **Step 1: Write the failing tests**

```python
# append to engine/tests/test_desktop_update.py
import io
import zipfile
from pathlib import Path

from atlas_engine.desktop_update import (
    UpdateApplyError,
    UpdateCheckResult,
    launch_swap_script,
    stage_update,
    write_swap_script,
)


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def test_stage_update_downloads_and_extracts_app_zip_only(tmp_path, monkeypatch):
    app_zip_bytes = _zip_bytes({"atlas-desktop.exe": b"new exe bytes"})
    import hashlib

    app_sha256 = hashlib.sha256(app_zip_bytes).hexdigest()

    result = UpdateCheckResult(
        update_available=True,
        latest_version="1.1.0",
        app_asset_url="https://example.test/app.zip",
        app_sha256=app_sha256,
        vendor_update_required=False,
    )

    class _FakeDownloadResponse:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeDownloadResponse(app_zip_bytes))

    staging_dir = tmp_path / "staging"
    stage_update(result, staging_dir)

    assert (staging_dir / "atlas-desktop.exe").read_bytes() == b"new exe bytes"
    assert not (staging_dir / "playwright").exists()


def test_stage_update_raises_on_checksum_mismatch(tmp_path, monkeypatch):
    app_zip_bytes = _zip_bytes({"atlas-desktop.exe": b"corrupted"})

    result = UpdateCheckResult(
        update_available=True,
        latest_version="1.1.0",
        app_asset_url="https://example.test/app.zip",
        app_sha256="0" * 64,
        vendor_update_required=False,
    )

    class _FakeDownloadResponse:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeDownloadResponse(app_zip_bytes))

    with pytest.raises(UpdateApplyError):
        stage_update(result, tmp_path / "staging")


def test_stage_update_also_extracts_vendor_zip_when_required(tmp_path, monkeypatch):
    import hashlib

    app_zip_bytes = _zip_bytes({"atlas-desktop.exe": b"exe"})
    vendor_zip_bytes = _zip_bytes({"playwright/driver/node.exe": b"node"})
    app_sha256 = hashlib.sha256(app_zip_bytes).hexdigest()
    vendor_sha256 = hashlib.sha256(vendor_zip_bytes).hexdigest()

    result = UpdateCheckResult(
        update_available=True,
        latest_version="1.1.0",
        app_asset_url="https://example.test/app.zip",
        app_sha256=app_sha256,
        vendor_update_required=True,
        vendor_asset_url="https://example.test/vendor.zip",
        vendor_sha256=vendor_sha256,
    )

    class _FakeDownloadResponse:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    def fake_get(url, **kwargs):
        if "vendor" in url:
            return _FakeDownloadResponse(vendor_zip_bytes)
        return _FakeDownloadResponse(app_zip_bytes)

    monkeypatch.setattr(httpx, "get", fake_get)

    staging_dir = tmp_path / "staging"
    stage_update(result, staging_dir)

    assert (staging_dir / "atlas-desktop.exe").read_bytes() == b"exe"
    assert (staging_dir / "playwright" / "driver" / "node.exe").read_bytes() == b"node"


def test_write_swap_script_contains_pid_and_paths(tmp_path):
    script_path = tmp_path / "swap.ps1"

    write_swap_script(
        pid=4242,
        staging_dir=tmp_path / "staging",
        install_dir=tmp_path / "install",
        exe_name="atlas-desktop.exe",
        script_path=script_path,
    )

    content = script_path.read_text()
    assert "4242" in content
    assert str(tmp_path / "staging") in content
    assert str(tmp_path / "install") in content
    assert "atlas-desktop.exe" in content
    assert "robocopy" in content.lower()


def test_launch_swap_script_invokes_powershell_detached(tmp_path, monkeypatch):
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "fake-process"

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    script_path = tmp_path / "swap.ps1"
    script_path.write_text("# placeholder")

    result = launch_swap_script(script_path)

    assert result == "fake-process"
    assert captured["args"][0] == "powershell"
    assert str(script_path) in captured["args"]
```

Add the two new imports this test file needs at its top: `import subprocess` and `import pytest` (if not already present from Task 1/2).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_desktop_update.py -v -k "stage_update or swap_script"`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write the implementation**

```python
# add to engine/src/atlas_engine/desktop_update.py
import hashlib
import subprocess
import zipfile
from pathlib import Path


class UpdateApplyError(Exception):
    pass


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download_and_verify(url: str, expected_sha256: str | None, dest_zip: Path) -> None:
    response = httpx.get(url, timeout=120.0, follow_redirects=True)
    response.raise_for_status()
    content = response.content
    if expected_sha256 is None or _sha256_bytes(content) != expected_sha256:
        raise UpdateApplyError(f"checksum mismatch downloading {url}")
    dest_zip.write_bytes(content)


def stage_update(result: UpdateCheckResult, staging_dir: Path) -> None:
    """Downloads and verifies this update's assets into staging_dir,
    extracted so staging_dir ends up laid out exactly like the install
    directory (app files at the top level; playwright/... only present
    when a vendor update was actually required). Raises UpdateApplyError
    on any checksum mismatch — never touches anything outside
    staging_dir, so the real install is untouched on failure."""
    staging_dir.mkdir(parents=True, exist_ok=True)

    app_zip = staging_dir / "_app.zip"
    _download_and_verify(result.app_asset_url, result.app_sha256, app_zip)
    with zipfile.ZipFile(app_zip) as zf:
        zf.extractall(staging_dir)
    app_zip.unlink()

    if result.vendor_update_required:
        vendor_zip = staging_dir / "_vendor.zip"
        _download_and_verify(result.vendor_asset_url, result.vendor_sha256, vendor_zip)
        with zipfile.ZipFile(vendor_zip) as zf:
            zf.extractall(staging_dir)
        vendor_zip.unlink()


_SWAP_SCRIPT_TEMPLATE = """\
$targetPid = {pid}
$staging = "{staging}"
$install = "{install}"
$exe = "{exe_name}"

while (Get-Process -Id $targetPid -ErrorAction SilentlyContinue) {{
    Start-Sleep -Milliseconds 500
}}

robocopy $staging $install /E | Out-Null
Remove-Item -Recurse -Force $staging
Start-Process -FilePath (Join-Path $install $exe)
"""


def write_swap_script(
    *, pid: int, staging_dir: Path, install_dir: Path, exe_name: str, script_path: Path
) -> None:
    """Plain (non-mirroring) robocopy: only files present in staging_dir
    are touched, so the install dir's existing playwright/ subfolder is
    left alone whenever a vendor update wasn't staged."""
    script_path.write_text(
        _SWAP_SCRIPT_TEMPLATE.format(
            pid=pid, staging=staging_dir, install=install_dir, exe_name=exe_name
        )
    )


def launch_swap_script(script_path: Path) -> subprocess.Popen:
    return subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
        close_fds=True,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_desktop_update.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/desktop_update.py engine/tests/test_desktop_update.py
git commit -m "feat: download, verify, and stage desktop updates; generate the swap script"
```

**Note for the implementer:** `launch_swap_script` deliberately doesn't pass Windows' `DETACHED_PROCESS`/`CREATE_NEW_PROCESS_GROUP` creation flags here because `subprocess.CREATE_NEW_PROCESS_GROUP` etc. don't exist as attributes on non-Windows platforms and this module is imported by the `engine-tests` CI job, which runs on `ubuntu-latest` — referencing them at module level would break every engine test on Linux. Task 4 adds those flags in `desktop/app.py` itself, conditionally, since that file only ever runs on the Windows build.

---

### Task 4: Wire the routes into the desktop app

**Files:**
- Create: `desktop/VERSION`
- Modify: `desktop/app.py`

**Interfaces:**
- Consumes: `check_for_update`, `stage_update`, `UpdateApplyError`, `write_swap_script`, `launch_swap_script` from `atlas_engine.desktop_update` (Tasks 1–3).
- Produces: `GET /update/check` → `dict` (the `UpdateCheckResult`'s fields); `POST /update/apply` → `{"ok": True}` on success, HTTP 400 if called in an unfrozen (dev) build, HTTP 409 if there's nothing to apply, HTTP 502 if staging/verification fails.

- [ ] **Step 1: Add `desktop/VERSION`**

```
1.0.0
```

(This is the first version tracked this way — bump it by hand before the next release that should actually publish.)

- [ ] **Step 2: Add the routes to `desktop/app.py`**

Add near the top, after the existing imports (`from atlas_engine.api import app as fastapi_app`):

```python
import dataclasses
import importlib.metadata
import tempfile

from atlas_engine.desktop_update import (
    UpdateApplyError,
    check_for_update,
    launch_swap_script,
    stage_update,
    write_swap_script,
)


def _read_app_version() -> str:
    return _resource_path("VERSION").read_text().strip()


def _read_vendor_version() -> str:
    return importlib.metadata.version("playwright")


@fastapi_app.get("/update/check")
def update_check_endpoint() -> dict:
    result = check_for_update(_read_app_version(), _read_vendor_version())
    return dataclasses.asdict(result)


@fastapi_app.post("/update/apply")
def update_apply_endpoint():
    if not getattr(sys, "frozen", False):
        raise HTTPException(status_code=400, detail="updates only apply to a packaged build")

    result = check_for_update(_read_app_version(), _read_vendor_version())
    if not result.update_available:
        raise HTTPException(status_code=409, detail="no update available")

    staging_dir = Path(tempfile.mkdtemp(prefix="atlas-desktop-update-"))
    try:
        stage_update(result, staging_dir)
    except UpdateApplyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    install_dir = Path(sys.executable).resolve().parent
    script_path = staging_dir.parent / f"{staging_dir.name}-swap.ps1"
    write_swap_script(
        pid=os.getpid(),
        staging_dir=staging_dir,
        install_dir=install_dir,
        exe_name="atlas-desktop.exe",
        script_path=script_path,
    )
    launch_swap_script(script_path)

    # Exit after a short delay, not immediately — the HTTP response below
    # must reach the frontend before this process disappears, otherwise
    # the "Update now" click looks like it silently failed.
    threading.Timer(1.0, lambda: os._exit(0)).start()
    return {"ok": True}
```

Needed new imports at the top of `desktop/app.py` alongside the existing `from fastapi.staticfiles import StaticFiles` line: add `from fastapi import HTTPException` (the file doesn't import `HTTPException` yet).

- [ ] **Step 3: Manual smoke test (unfrozen dev run)**

Run: `cd engine && .venv/bin/python ../desktop/app.py` (requires `engine/.env` configured per the repo's normal dev setup), then in another terminal:

```bash
curl -s http://localhost:8000/update/check | python3 -m json.tool
```

Expected: a JSON object with `"update_available": false` (since no `desktop-v*` GitHub release exists yet) — confirms the route is wired and doesn't crash. Then:

```bash
curl -s -X POST http://localhost:8000/update/apply
```

Expected: HTTP 400, `{"detail": "updates only apply to a packaged build"}` — confirms the frozen-build guard works.

- [ ] **Step 4: Commit**

```bash
git add desktop/VERSION desktop/app.py
git commit -m "feat: add /update/check and /update/apply routes to the desktop app"
```

---

### Task 5: Publish versioned, split releases from CI

**Files:**
- Modify: `.github/workflows/build-exe.yml`

**Interfaces:**
- Consumes: `desktop/VERSION` (Task 4).
- Produces: a GitHub Release tagged `desktop-v<version>` with assets `atlas-desktop-app-<version>.zip`, `atlas-desktop-vendor-<playwright_version>.zip`, `checksums.txt`, published only when `desktop/VERSION` differs from every already-published `desktop-v*` tag.

- [ ] **Step 1: Bundle `desktop/VERSION` into the PyInstaller build**

`desktop/app.py`'s `_read_app_version()` (Task 4) reads `VERSION` via `_resource_path`, which only finds it in a frozen build if it was passed to PyInstaller the same way `desktop/production.env` already is. In the existing `Build desktop exe` step, change:

```yaml
          pyinstaller --onedir --windowed --name atlas-desktop --add-data "frontend/dist;frontend_dist" --add-data "desktop/production.env;." --paths engine/src desktop/app.py
```

to:

```yaml
          pyinstaller --onedir --windowed --name atlas-desktop --add-data "frontend/dist;frontend_dist" --add-data "desktop/production.env;." --add-data "desktop/VERSION;." --paths engine/src desktop/app.py
```

- [ ] **Step 2: Replace the zip/upload tail of the workflow**

Replace everything from `- name: Zip distribution folder` through the end of the file with:

```yaml
      - name: Read desktop version
        id: version
        shell: bash
        run: echo "value=$(cat desktop/VERSION)" >> "$GITHUB_OUTPUT"

      - name: Read vendor (Playwright) version
        id: vendor
        shell: bash
        run: |
          value=$(python -c "import importlib.metadata; print(importlib.metadata.version('playwright'))")
          echo "value=$value" >> "$GITHUB_OUTPUT"

      - name: Check whether this version was already released
        id: existing
        shell: bash
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          if gh release view "desktop-v${{ steps.version.outputs.value }}" >/dev/null 2>&1; then
            echo "already_released=true" >> "$GITHUB_OUTPUT"
          else
            echo "already_released=false" >> "$GITHUB_OUTPUT"
          fi

      - name: Split into app and vendor zips
        if: steps.existing.outputs.already_released == 'false'
        shell: bash
        run: |
          set -euo pipefail
          app_version="${{ steps.version.outputs.value }}"
          vendor_version="${{ steps.vendor.outputs.value }}"

          mkdir -p dist/app-payload/_internal
          cd dist/atlas-desktop
          find . -mindepth 1 -maxdepth 1 ! -name '_internal' -exec cp -r {} ../app-payload/ \;
          find _internal -mindepth 1 -maxdepth 1 ! -name 'playwright' -exec cp -r {} ../app-payload/_internal/ \;
          cd ../app-payload
          7z a "../atlas-desktop-app-${app_version}.zip" . >/dev/null
          cd ../atlas-desktop/_internal
          7z a "../../atlas-desktop-vendor-${vendor_version}.zip" playwright >/dev/null
          cd ../../..

          sha256sum "dist/atlas-desktop-app-${app_version}.zip" "dist/atlas-desktop-vendor-${vendor_version}.zip" \
            | sed 's#dist/##' > dist/checksums.txt

      - name: Publish GitHub release
        if: steps.existing.outputs.already_released == 'false'
        shell: bash
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          app_version="${{ steps.version.outputs.value }}"
          vendor_version="${{ steps.vendor.outputs.value }}"
          gh release create "desktop-v${app_version}" \
            "dist/atlas-desktop-app-${app_version}.zip" \
            "dist/atlas-desktop-vendor-${vendor_version}.zip" \
            dist/checksums.txt \
            --title "Atlas Desktop v${app_version}" \
            --notes "Automated Windows build."

      - name: Upload exe artifact (optional fallback)
        continue-on-error: true
        uses: actions/upload-artifact@v4
        with:
          name: atlas-desktop-windows
          path: dist/atlas-desktop/
          include-hidden-files: true
```

This drops the old unconditional `gh release create latest-windows ... || gh release upload latest-windows --clobber` step entirely — every push to `main` still builds (so the build itself stays a regression check), but a new versioned release is only published when `desktop/VERSION` has actually changed.

- [ ] **Step 3: Validate the YAML parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/build-exe.yml'))"`
Expected: no output, exit code 0.

- [ ] **Step 4: Dry-run the split logic locally against a fake bundle**

This can't exercise the real Windows runner, `7z`, or `gh`, but it proves the `find`/copy logic picks the right files before trusting it to CI:

```bash
mkdir -p /tmp/fake-dist/atlas-desktop/_internal/playwright/driver
mkdir -p /tmp/fake-dist/atlas-desktop/_internal/cryptography
touch /tmp/fake-dist/atlas-desktop/atlas-desktop.exe
touch /tmp/fake-dist/atlas-desktop/_internal/playwright/driver/node.exe
touch /tmp/fake-dist/atlas-desktop/_internal/cryptography/_rust.pyd

mkdir -p /tmp/fake-dist/app-payload/_internal
cd /tmp/fake-dist/atlas-desktop
find . -mindepth 1 -maxdepth 1 ! -name '_internal' -exec cp -r {} ../app-payload/ \;
find _internal -mindepth 1 -maxdepth 1 ! -name 'playwright' -exec cp -r {} ../app-payload/_internal/ \;
cd /tmp/fake-dist
find app-payload
```

Expected output lists `app-payload/atlas-desktop.exe` and `app-payload/_internal/cryptography/_rust.pyd`, and does **not** list anything under `app-payload/_internal/playwright/`. Clean up afterward: `rm -rf /tmp/fake-dist`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/build-exe.yml
git commit -m "ci: publish versioned, split app/vendor desktop releases"
```

**Note for the implementer:** this task's real verification only happens on the next push to `main` that bumps `desktop/VERSION` — don't push a `desktop/VERSION` bump as part of this plan's execution; that's a deliberate, separate decision for whoever ships the first version through this pipeline, not something to trigger automatically while implementing it.

---

### Task 6: Frontend update-check hooks

**Files:**
- Create: `frontend/src/lib/update.ts`
- Create: `frontend/src/lib/update.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` from `frontend/src/lib/api.ts`.
- Produces: `interface UpdateCheckResult` (mirrors the backend's dict: `update_available`, `latest_version`, `notes`, `app_asset_url`, `app_sha256`, `vendor_update_required`, `vendor_asset_url`, `vendor_sha256` — all nullable except `update_available`/`vendor_update_required`); `useUpdateCheck()` (a `useQuery` with key `['update-check']`, `retry: false`); `useApplyUpdate()` (a `useMutation` POSTing `/update/apply`).

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/lib/update.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { useApplyUpdate, useUpdateCheck } from './update'

let queryClient: QueryClient

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

describe('useUpdateCheck', () => {
  it('returns the update check result', async () => {
    vi.stubGlobal('fetch', vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ update_available: true, latest_version: '1.1.0', notes: 'fixes', app_asset_url: 'https://x/app.zip' }),
      }),
    ))

    const { result } = renderHook(() => useUpdateCheck(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.update_available).toBe(true)
    expect(result.current.data?.latest_version).toBe('1.1.0')
  })

  it('does not throw when the endpoint 404s (web deployment without the desktop routes)', async () => {
    vi.stubGlobal('fetch', vi.fn(() =>
      Promise.resolve({ ok: false, status: 404, json: async () => ({ detail: 'not found' }) }),
    ))

    const { result } = renderHook(() => useUpdateCheck(), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.data).toBeUndefined()
  })
})

describe('useApplyUpdate', () => {
  it('POSTs to /update/apply', async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      expect(String(url)).toContain('/update/apply')
      expect(init?.method).toBe('POST')
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ ok: true }) })
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useApplyUpdate(), { wrapper })
    result.current.mutate()

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- update.test.tsx`
Expected: FAIL with "Failed to resolve import './update'"

- [ ] **Step 3: Write the implementation**

```ts
// frontend/src/lib/update.ts
import { useMutation, useQuery } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface UpdateCheckResult {
  update_available: boolean
  latest_version: string | null
  notes: string | null
  app_asset_url: string | null
  app_sha256: string | null
  vendor_update_required: boolean
  vendor_asset_url: string | null
  vendor_sha256: string | null
}

export function useUpdateCheck() {
  return useQuery({
    queryKey: ['update-check'],
    queryFn: () => apiFetch<UpdateCheckResult>('/update/check'),
    retry: false,
    staleTime: Infinity,
  })
}

export function useApplyUpdate() {
  return useMutation({
    mutationFn: () => apiFetch<{ ok: boolean }>('/update/apply', { method: 'POST' }),
  })
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- update.test.tsx`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/update.ts frontend/src/lib/update.test.tsx
git commit -m "feat: add useUpdateCheck/useApplyUpdate hooks"
```

---

### Task 7: Update banner

**Files:**
- Create: `frontend/src/components/UpdateBanner.tsx`
- Create: `frontend/src/components/UpdateBanner.test.tsx`
- Modify: `frontend/src/layout/AppLayout.tsx`

**Interfaces:**
- Consumes: `useUpdateCheck`, `useApplyUpdate` from `frontend/src/lib/update.ts` (Task 6).
- Produces: `UpdateBanner` component (no props), mounted in `AppLayout` above `<Outlet />`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/UpdateBanner.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { UpdateBanner } from './UpdateBanner'

function renderBanner() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <UpdateBanner />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/update/check') {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({
          update_available: true,
          latest_version: '1.1.0',
          notes: 'bug fixes',
          app_asset_url: 'https://x/app.zip',
        }),
      })
    }
    if (path === '/update/apply' && init?.method === 'POST') {
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ ok: true }) })
    }
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

describe('UpdateBanner', () => {
  it('shows the available version and notes', async () => {
    renderBanner()
    expect(await screen.findByText(/1\.1\.0/)).toBeInTheDocument()
    expect(screen.getByText('bug fixes')).toBeInTheDocument()
  })

  it('renders nothing when no update is available', async () => {
    vi.stubGlobal('fetch', vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, json: async () => ({ update_available: false }) }),
    ))
    const { container } = renderBanner()
    await waitFor(() => expect(container).toBeEmptyDOMElement())
  })

  it('dismisses on click', async () => {
    renderBanner()
    const dismissButton = await screen.findByText('Dismiss')
    await userEvent.click(dismissButton)
    expect(screen.queryByText(/1\.1\.0/)).toBeNull()
  })

  it('calls apply when "Update now" is clicked', async () => {
    renderBanner()
    const updateButton = await screen.findByText('Update now')
    await userEvent.click(updateButton)
    await waitFor(() => expect(screen.getByText('Updating…')).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- UpdateBanner.test.tsx`
Expected: FAIL with "Failed to resolve import './UpdateBanner'"

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/src/components/UpdateBanner.tsx
import { useState } from 'react'
import { useApplyUpdate, useUpdateCheck } from '../lib/update'

export function UpdateBanner() {
  const { data } = useUpdateCheck()
  const applyUpdate = useApplyUpdate()
  const [dismissed, setDismissed] = useState(false)

  if (!data?.update_available || dismissed) return null

  return (
    <div className="flex items-center justify-between gap-3 bg-atlas-accentSubtle border-b border-atlas-border px-4 py-2 text-[13px]">
      <div>
        <span className="font-semibold">Version {data.latest_version} available.</span>
        {data.notes && <span className="text-atlas-muted ml-2">{data.notes}</span>}
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={() => applyUpdate.mutate()}
          disabled={applyUpdate.isPending}
          className="px-3 py-1 rounded-[8px] bg-atlas-primary text-white text-[12.5px] font-medium disabled:opacity-60"
        >
          {applyUpdate.isPending ? 'Updating…' : 'Update now'}
        </button>
        <button onClick={() => setDismissed(true)} className="text-atlas-muted text-[12.5px]">
          Dismiss
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- UpdateBanner.test.tsx`
Expected: PASS (4 tests)

- [ ] **Step 5: Mount it in `AppLayout`**

In `frontend/src/layout/AppLayout.tsx`, add the import:

```tsx
import { UpdateBanner } from '../components/UpdateBanner'
```

And change:

```tsx
      <div className="flex-1 min-w-0 flex flex-col">
        <Outlet />
      </div>
```

to:

```tsx
      <div className="flex-1 min-w-0 flex flex-col">
        <UpdateBanner />
        <Outlet />
      </div>
```

- [ ] **Step 6: Run the full frontend suite**

Run: `cd frontend && npm test`
Expected: PASS, including the existing `AppLayout.test.tsx` (it doesn't stub a `/update/check` response, so `useUpdateCheck` will reject — confirm that test still passes, since the banner renders nothing on error; if it fails, add a `/update/check` stub returning `{ update_available: false }` to that test's existing fetch mock).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/UpdateBanner.tsx frontend/src/components/UpdateBanner.test.tsx frontend/src/layout/AppLayout.tsx
git commit -m "feat: show an update banner when a newer desktop build is available"
```

---

## Manual end-to-end verification (not part of any task's automated tests)

Once all 7 tasks are merged and a real `desktop-v*` release has been published by CI (requires bumping `desktop/VERSION` and pushing — a deliberate, separate step, per Task 5's note):

1. Install an older version on a real Windows machine.
2. Launch it, confirm the banner appears with the correct version/notes.
3. Click "Update now", confirm the app closes and relaunches automatically with no manual file handling, and the new version is what's running (`GET /update/check` now reports `update_available: false`).
4. Repeat once with a release that also bumps the vendor zip (requires bumping the `playwright` pin in `engine/pyproject.toml`), confirming the vendor payload is downloaded and the swap still completes correctly.

This is the step that actually proves the Windows-specific pieces (`robocopy`, process-exit timing, `Start-Process` relaunch) work — nothing in Tasks 1–7 can exercise them without a real Windows machine and a real packaged build.
