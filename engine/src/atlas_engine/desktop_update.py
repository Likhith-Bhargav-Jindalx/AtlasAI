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
