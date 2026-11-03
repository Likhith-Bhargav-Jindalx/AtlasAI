"""Checks GitHub Releases for a newer desktop build and applies it in
place. Only used by the packaged Windows desktop exe (desktop/app.py) —
not part of the web-hosted deployment's API surface, and not imported
by atlas_engine.api.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

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
        result[filename.strip()] = digest.strip()
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
