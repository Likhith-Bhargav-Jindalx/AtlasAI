import hashlib
import io
import subprocess
import zipfile
from pathlib import Path

import httpx
import pytest

from atlas_engine.desktop_update import (
    UpdateApplyError,
    UpdateCheckResult,
    check_for_update,
    is_newer,
    launch_swap_script,
    parse_checksums,
    parse_vendor_version_from_filename,
    parse_version,
    stage_update,
    write_swap_script,
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


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


class _FakeDownloadResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


def test_stage_update_downloads_and_extracts_app_zip_only(tmp_path, monkeypatch):
    app_zip_bytes = _zip_bytes({"atlas-desktop.exe": b"new exe bytes"})
    app_sha256 = hashlib.sha256(app_zip_bytes).hexdigest()

    result = UpdateCheckResult(
        update_available=True,
        latest_version="1.1.0",
        app_asset_url="https://example.test/app.zip",
        app_sha256=app_sha256,
        vendor_update_required=False,
    )

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

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeDownloadResponse(app_zip_bytes))

    with pytest.raises(UpdateApplyError):
        stage_update(result, tmp_path / "staging")


def test_stage_update_also_extracts_vendor_zip_when_required(tmp_path, monkeypatch):
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
