"""Checks GitHub Releases for a newer desktop build and applies it in
place. Only used by the packaged Windows desktop exe (desktop/app.py) —
not part of the web-hosted deployment's API surface, and not imported
by atlas_engine.api.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

# phase ("app" | "vendor"), bytes downloaded so far, total bytes (None if the
# server didn't send Content-Length).
ProgressCallback = Callable[[str, int, int | None], None]

GITHUB_RELEASES_LATEST_URL = (
    "https://api.github.com/repos/Likhith-Bhargav-Jindalx/AtlasAI/releases/latest"
)
_REQUEST_TIMEOUT_SECONDS = 3.0


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
        # sha256sum's binary-mode format prefixes the filename with "*"
        # (the Windows CI runner's sha256sum uses this by default); text
        # mode doesn't. Strip it either way so lookups by plain filename work.
        result[filename.strip().lstrip("*")] = digest.strip()
    return result


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

        # GitHub release asset URLs 302-redirect to signed blob storage URLs —
        # without follow_redirects, raise_for_status() treats that redirect
        # itself as an error and this whole check silently reports no update.
        checksums_response = httpx.get(checksums_url, timeout=_REQUEST_TIMEOUT_SECONDS, follow_redirects=True)
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


class UpdateApplyError(Exception):
    pass


def _download_and_verify(
    url: str,
    expected_sha256: str | None,
    dest_zip: Path,
    *,
    phase: str,
    on_progress: ProgressCallback | None,
) -> None:
    # Streamed (not httpx.get(...).content) so a large vendor zip (hundreds
    # of MB) is never held in memory whole, and so on_progress can be called
    # as bytes arrive instead of only once at the end.
    hasher = hashlib.sha256()
    downloaded = 0
    with dest_zip.open("wb") as f:
        with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as response:
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            total_bytes = int(content_length) if content_length is not None else None
            for chunk in response.iter_bytes():
                f.write(chunk)
                hasher.update(chunk)
                downloaded += len(chunk)
                if on_progress is not None:
                    on_progress(phase, downloaded, total_bytes)

    if expected_sha256 is None or hasher.hexdigest() != expected_sha256:
        dest_zip.unlink(missing_ok=True)
        raise UpdateApplyError(f"checksum mismatch downloading {url}")


def stage_update(
    result: UpdateCheckResult, staging_dir: Path, on_progress: ProgressCallback | None = None
) -> None:
    """Downloads and verifies this update's assets into staging_dir,
    extracted so staging_dir ends up laid out exactly like the install
    directory (app files at the top level; _internal/playwright/... only
    present when a vendor update was actually required). Raises
    UpdateApplyError on any checksum mismatch — never touches anything
    outside staging_dir, so the real install is untouched on failure."""
    staging_dir.mkdir(parents=True, exist_ok=True)

    app_zip = staging_dir / "_app.zip"
    _download_and_verify(result.app_asset_url, result.app_sha256, app_zip, phase="app", on_progress=on_progress)
    with zipfile.ZipFile(app_zip) as zf:
        zf.extractall(staging_dir)
    app_zip.unlink()

    if result.vendor_update_required:
        vendor_zip = staging_dir / "_vendor.zip"
        _download_and_verify(
            result.vendor_asset_url, result.vendor_sha256, vendor_zip, phase="vendor", on_progress=on_progress
        )
        # The vendor zip's own root is bare "playwright/" (it's zipped from
        # inside the build's _internal/ dir — see build-exe.yml's "Split
        # into app and vendor zips" step), but the real install's
        # playwright/ lives at _internal/playwright/. Extracting straight
        # into staging_dir would land it at staging_dir/playwright instead
        # — a stray folder robocopy would then create next to _internal/
        # rather than inside it, silently leaving the real _internal/playwright/
        # (and its driver code) untouched by every vendor update.
        vendor_extract_dir = staging_dir / "_internal"
        vendor_extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(vendor_zip) as zf:
            zf.extractall(vendor_extract_dir)
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
