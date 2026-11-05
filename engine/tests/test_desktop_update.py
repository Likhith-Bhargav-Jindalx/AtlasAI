import hashlib
import io
import json
import subprocess
import zipfile

import pytest
from azure.core.exceptions import ResourceNotFoundError

from atlas_engine import desktop_update as du
from atlas_engine.desktop_update import (
    UpdateApplyError,
    UpdateCheckResult,
    check_for_update,
    is_newer,
    launch_swap_script,
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


class _FakeDownloader:
    def __init__(self, content: bytes):
        self._content = content
        self.size = len(content)

    def readall(self) -> bytes:
        return self._content

    def chunks(self, chunk_size: int = 65536):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i : i + chunk_size]


class _FakeContainerClient:
    def __init__(self, blobs: dict[str, bytes]):
        self._blobs = blobs

    def download_blob(self, name: str) -> _FakeDownloader:
        if name not in self._blobs:
            raise ResourceNotFoundError(f"no such blob: {name}")
        return _FakeDownloader(self._blobs[name])


class _FakeBlobServiceClient:
    def __init__(self, container_client: _FakeContainerClient):
        self._container_client = container_client

    def get_container_client(self, container: str) -> _FakeContainerClient:
        return self._container_client


def _patch_blobs(monkeypatch, blobs: dict[str, bytes]) -> _FakeContainerClient:
    container_client = _FakeContainerClient(blobs)
    monkeypatch.setattr(
        du.BlobServiceClient,
        "from_connection_string",
        staticmethod(lambda connection_string: _FakeBlobServiceClient(container_client)),
    )
    return container_client


def _manifest_bytes(version, vendor_version, vendor_blob=None, notes="release notes") -> bytes:
    manifest = {
        "version": version,
        "notes": notes,
        "app_blob": f"desktop-releases/v{version}/atlas-desktop-app-{version}.zip",
        "app_sha256": "aaa111",
        "vendor_version": vendor_version,
    }
    if vendor_blob:
        manifest["vendor_blob"] = vendor_blob
        manifest["vendor_sha256"] = "bbb222"
    return json.dumps(manifest).encode()


def test_check_for_update_reports_no_update_without_connection_string():
    result = check_for_update(local_version="1.0.0", local_vendor_version="1.49.0")

    assert result == UpdateCheckResult(update_available=False)


def test_check_for_update_reports_no_update_when_already_current(monkeypatch):
    _patch_blobs(monkeypatch, {"desktop-releases/latest.json": _manifest_bytes("1.0.0", "1.49.0")})

    result = check_for_update(local_version="1.0.0", local_vendor_version="1.49.0", connection_string="fake-conn")

    assert result == UpdateCheckResult(update_available=False)


def test_check_for_update_reports_available_update_without_vendor_change(monkeypatch):
    _patch_blobs(monkeypatch, {"desktop-releases/latest.json": _manifest_bytes("1.1.0", "1.49.0")})

    result = check_for_update(local_version="1.0.0", local_vendor_version="1.49.0", connection_string="fake-conn")

    assert result.update_available is True
    assert result.latest_version == "1.1.0"
    assert result.notes == "release notes"
    assert result.app_blob == "desktop-releases/v1.1.0/atlas-desktop-app-1.1.0.zip"
    assert result.app_sha256 == "aaa111"
    assert result.vendor_update_required is False
    assert result.vendor_blob is None
    assert result.vendor_sha256 is None


def test_check_for_update_requires_vendor_zip_when_vendor_version_changed(monkeypatch):
    vendor_blob = "desktop-releases/v1.1.0/atlas-desktop-vendor-1.50.0.zip"
    _patch_blobs(
        monkeypatch,
        {"desktop-releases/latest.json": _manifest_bytes("1.1.0", "1.50.0", vendor_blob=vendor_blob)},
    )

    result = check_for_update(local_version="1.0.0", local_vendor_version="1.49.0", connection_string="fake-conn")

    assert result.vendor_update_required is True
    assert result.vendor_blob == vendor_blob
    assert result.vendor_sha256 == "bbb222"


def test_check_for_update_never_raises_when_manifest_missing(monkeypatch):
    _patch_blobs(monkeypatch, {})

    result = check_for_update(local_version="1.0.0", local_vendor_version="1.49.0", connection_string="fake-conn")

    assert result == UpdateCheckResult(update_available=False)


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def test_stage_update_downloads_and_extracts_app_zip_only(tmp_path, monkeypatch):
    app_zip_bytes = _zip_bytes({"atlas-desktop.exe": b"new exe bytes"})
    app_sha256 = hashlib.sha256(app_zip_bytes).hexdigest()
    _patch_blobs(monkeypatch, {"app.zip": app_zip_bytes})

    result = UpdateCheckResult(
        update_available=True,
        latest_version="1.1.0",
        app_blob="app.zip",
        app_sha256=app_sha256,
        vendor_update_required=False,
    )

    staging_dir = tmp_path / "staging"
    stage_update(result, staging_dir, connection_string="fake-conn")

    assert (staging_dir / "atlas-desktop.exe").read_bytes() == b"new exe bytes"
    assert not (staging_dir / "playwright").exists()


def test_stage_update_reports_download_progress(tmp_path, monkeypatch):
    app_zip_bytes = _zip_bytes({"atlas-desktop.exe": b"x" * 200_000})
    app_sha256 = hashlib.sha256(app_zip_bytes).hexdigest()
    _patch_blobs(monkeypatch, {"app.zip": app_zip_bytes})

    result = UpdateCheckResult(
        update_available=True,
        latest_version="1.1.0",
        app_blob="app.zip",
        app_sha256=app_sha256,
        vendor_update_required=False,
    )

    progress_calls = []
    stage_update(
        result,
        tmp_path / "staging",
        connection_string="fake-conn",
        on_progress=lambda *args: progress_calls.append(args),
    )

    assert progress_calls[0][0] == "app"
    assert progress_calls[-1] == ("app", len(app_zip_bytes), len(app_zip_bytes))
    # Reported incrementally, not just once at the end.
    assert len(progress_calls) > 1


def test_stage_update_raises_on_checksum_mismatch(tmp_path, monkeypatch):
    app_zip_bytes = _zip_bytes({"atlas-desktop.exe": b"corrupted"})
    _patch_blobs(monkeypatch, {"app.zip": app_zip_bytes})

    result = UpdateCheckResult(
        update_available=True,
        latest_version="1.1.0",
        app_blob="app.zip",
        app_sha256="0" * 64,
        vendor_update_required=False,
    )

    staging_dir = tmp_path / "staging"
    with pytest.raises(UpdateApplyError):
        stage_update(result, staging_dir, connection_string="fake-conn")

    assert not (staging_dir / "_app.zip").exists()


def test_stage_update_also_extracts_vendor_zip_when_required(tmp_path, monkeypatch):
    app_zip_bytes = _zip_bytes({"atlas-desktop.exe": b"exe"})
    vendor_zip_bytes = _zip_bytes({"playwright/driver/node.exe": b"node"})
    app_sha256 = hashlib.sha256(app_zip_bytes).hexdigest()
    vendor_sha256 = hashlib.sha256(vendor_zip_bytes).hexdigest()
    _patch_blobs(monkeypatch, {"app.zip": app_zip_bytes, "vendor.zip": vendor_zip_bytes})

    result = UpdateCheckResult(
        update_available=True,
        latest_version="1.1.0",
        app_blob="app.zip",
        app_sha256=app_sha256,
        vendor_update_required=True,
        vendor_blob="vendor.zip",
        vendor_sha256=vendor_sha256,
    )

    progress_calls = []
    staging_dir = tmp_path / "staging"
    stage_update(
        result, staging_dir, connection_string="fake-conn", on_progress=lambda *args: progress_calls.append(args)
    )

    assert (staging_dir / "atlas-desktop.exe").read_bytes() == b"exe"
    assert (staging_dir / "playwright" / "driver" / "node.exe").read_bytes() == b"node"
    assert {phase for phase, _, _ in progress_calls} == {"app", "vendor"}


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
