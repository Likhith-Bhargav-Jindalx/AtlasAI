"""Checks Azure Blob Storage for a newer desktop build and applies it in
place. Only used by the packaged Windows desktop exe (desktop/app.py) —
not part of the web-hosted deployment's API surface, and not imported by
atlas_engine.api.

Deliberately not GitHub Releases: this exe's release repo can be private
and can change owner, and a private repo's Releases API needs a token
whose access an org admin may have to approve — neither applies to blob
storage the build pipeline's own account already owns.

Reuses the SAME ATLAS_AZURE_STORAGE_CONNECTION_STRING already baked into
every build for the app's own Postgres/Blob access (see desktop/app.py
and desktop/build.sh) rather than minting a separate scoped SAS token: a
second, narrower credential would add nothing, since the full
account-key connection string already baked alongside it grants strictly
more access than any read-only SAS could. Release files live under a
`desktop-releases/` prefix inside the existing `atlas-runs` container —
not a security boundary (the same connection string already reads/writes
all of it), just keeping release artifacts out of the recordings listing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from azure.storage.blob import BlobServiceClient

logger = logging.getLogger(__name__)

# phase ("app" | "vendor"), bytes downloaded so far, total bytes (None if the
# server didn't send Content-Length).
ProgressCallback = Callable[[str, int, int | None], None]

RELEASE_PREFIX = "desktop-releases"


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


@dataclass
class UpdateCheckResult:
    update_available: bool
    latest_version: str | None = None
    notes: str | None = None
    app_blob: str | None = None
    app_sha256: str | None = None
    vendor_update_required: bool = False
    vendor_blob: str | None = None
    vendor_sha256: str | None = None


def _manifest_blob_name() -> str:
    return f"{RELEASE_PREFIX}/latest.json"


def check_for_update(
    local_version: str,
    local_vendor_version: str,
    connection_string: str | None = None,
    container: str = "atlas-runs",
) -> UpdateCheckResult:
    if not connection_string:
        return UpdateCheckResult(update_available=False)

    try:
        container_client = BlobServiceClient.from_connection_string(connection_string).get_container_client(
            container
        )
        manifest = json.loads(container_client.download_blob(_manifest_blob_name()).readall())

        remote_version: str = manifest["version"]
        if not is_newer(remote_version, local_version):
            return UpdateCheckResult(update_available=False)

        remote_vendor_version = manifest.get("vendor_version")
        vendor_blob = manifest.get("vendor_blob")
        vendor_update_required = bool(vendor_blob) and remote_vendor_version != local_vendor_version

        return UpdateCheckResult(
            update_available=True,
            latest_version=remote_version,
            notes=manifest.get("notes") or "",
            app_blob=manifest["app_blob"],
            app_sha256=manifest.get("app_sha256"),
            vendor_update_required=vendor_update_required,
            vendor_blob=vendor_blob if vendor_update_required else None,
            vendor_sha256=manifest.get("vendor_sha256") if vendor_update_required else None,
        )
    except Exception:
        logger.warning("desktop update check failed", exc_info=True)
        return UpdateCheckResult(update_available=False)


class UpdateApplyError(Exception):
    pass


def _download_and_verify(
    container_client,
    blob_name: str,
    expected_sha256: str | None,
    dest_zip: Path,
    *,
    phase: str,
    on_progress: ProgressCallback | None,
) -> None:
    # Streamed chunk-by-chunk (not .readall()) so a large vendor zip
    # (hundreds of MB) is never held in memory whole, and so on_progress can
    # be called as bytes arrive instead of only once at the end.
    hasher = hashlib.sha256()
    downloaded = 0
    downloader = container_client.download_blob(blob_name)
    total_bytes = downloader.size
    with dest_zip.open("wb") as f:
        for chunk in downloader.chunks():
            f.write(chunk)
            hasher.update(chunk)
            downloaded += len(chunk)
            if on_progress is not None:
                on_progress(phase, downloaded, total_bytes)

    if expected_sha256 is None or hasher.hexdigest() != expected_sha256:
        dest_zip.unlink(missing_ok=True)
        raise UpdateApplyError(f"checksum mismatch downloading {blob_name}")


def stage_update(
    result: UpdateCheckResult,
    staging_dir: Path,
    connection_string: str,
    container: str = "atlas-runs",
    on_progress: ProgressCallback | None = None,
) -> None:
    """Downloads and verifies this update's assets into staging_dir,
    extracted so staging_dir ends up laid out exactly like the install
    directory (app files at the top level; playwright/... only present
    when a vendor update was actually required). Raises UpdateApplyError
    on any checksum mismatch — never touches anything outside
    staging_dir, so the real install is untouched on failure."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    container_client = BlobServiceClient.from_connection_string(connection_string).get_container_client(container)

    app_zip = staging_dir / "_app.zip"
    _download_and_verify(
        container_client, result.app_blob, result.app_sha256, app_zip, phase="app", on_progress=on_progress
    )
    with zipfile.ZipFile(app_zip) as zf:
        zf.extractall(staging_dir)
    app_zip.unlink()

    if result.vendor_update_required:
        vendor_zip = staging_dir / "_vendor.zip"
        _download_and_verify(
            container_client,
            result.vendor_blob,
            result.vendor_sha256,
            vendor_zip,
            phase="vendor",
            on_progress=on_progress,
        )
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
