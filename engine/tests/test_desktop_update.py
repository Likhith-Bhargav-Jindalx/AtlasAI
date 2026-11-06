import hashlib
import io
import subprocess
import zipfile
from pathlib import Path

import httpx
import pytest

from atlas_engine.desktop_update import (
    _REQUEST_TIMEOUT_SECONDS,
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


def test_parse_checksums_strips_sha256sum_binary_mode_asterisk():
    # Real format from the Windows CI runner's sha256sum (confirmed against
    # a real published release's checksums.txt): "<hex> *<filename>", not
    # "<hex>  <filename>".
    text = (
        "274a73d2a58dc039d62d06e2fe875bfe28b00cd7bfc04c9b114d451033bd26da *atlas-desktop-app-1.0.0.zip\n"
        "55f4fdc9bdda1dc1f06fc8c2cb8f37c9a97001f2103b153ab220f8b0a8669f19 *atlas-desktop-vendor-1.63.0.zip\n"
    )
    assert parse_checksums(text) == {
        "atlas-desktop-app-1.0.0.zip": "274a73d2a58dc039d62d06e2fe875bfe28b00cd7bfc04c9b114d451033bd26da",
        "atlas-desktop-vendor-1.63.0.zip": "55f4fdc9bdda1dc1f06fc8c2cb8f37c9a97001f2103b153ab220f8b0a8669f19",
    }


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

    checksums_calls = []

    def fake_get(url, **kwargs):
        if url.endswith("checksums.txt"):
            checksums_calls.append(kwargs)
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
    # Regression: GitHub asset URLs 302-redirect to signed blob storage URLs.
    # Without follow_redirects=True, a real httpx call's raise_for_status()
    # treats the unfollowed redirect itself as an error and this whole
    # check silently reports no update — this mock wouldn't catch that on
    # its own, so assert the call site actually asks to follow redirects.
    assert checksums_calls == [{"timeout": _REQUEST_TIMEOUT_SECONDS, "follow_redirects": True}]


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


class _FakeStreamResponse:
    def __init__(self, content: bytes):
        self._content = content
        self.headers = {"content-length": str(len(content))}

    def raise_for_status(self):
        pass

    def iter_bytes(self, chunk_size: int = 65536):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i : i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


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

    monkeypatch.setattr(httpx, "stream", lambda method, url, **k: _FakeStreamResponse(app_zip_bytes))

    staging_dir = tmp_path / "staging"
    stage_update(result, staging_dir)

    assert (staging_dir / "atlas-desktop.exe").read_bytes() == b"new exe bytes"
    assert not (staging_dir / "playwright").exists()


def test_stage_update_reports_download_progress(tmp_path, monkeypatch):
    app_zip_bytes = _zip_bytes({"atlas-desktop.exe": b"x" * 200_000})
    app_sha256 = hashlib.sha256(app_zip_bytes).hexdigest()

    result = UpdateCheckResult(
        update_available=True,
        latest_version="1.1.0",
        app_asset_url="https://example.test/app.zip",
        app_sha256=app_sha256,
        vendor_update_required=False,
    )

    monkeypatch.setattr(httpx, "stream", lambda method, url, **k: _FakeStreamResponse(app_zip_bytes))

    progress_calls = []
    stage_update(result, tmp_path / "staging", on_progress=lambda *args: progress_calls.append(args))

    assert progress_calls[0][0] == "app"
    assert progress_calls[-1] == ("app", len(app_zip_bytes), len(app_zip_bytes))
    # Reported incrementally, not just once at the end.
    assert len(progress_calls) > 1


def test_stage_update_raises_on_checksum_mismatch(tmp_path, monkeypatch):
    app_zip_bytes = _zip_bytes({"atlas-desktop.exe": b"corrupted"})

    result = UpdateCheckResult(
        update_available=True,
        latest_version="1.1.0",
        app_asset_url="https://example.test/app.zip",
        app_sha256="0" * 64,
        vendor_update_required=False,
    )

    monkeypatch.setattr(httpx, "stream", lambda method, url, **k: _FakeStreamResponse(app_zip_bytes))

    staging_dir = tmp_path / "staging"
    with pytest.raises(UpdateApplyError):
        stage_update(result, staging_dir)

    assert not (staging_dir / "_app.zip").exists()


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

    def fake_stream(method, url, **kwargs):
        if "vendor" in url:
            return _FakeStreamResponse(vendor_zip_bytes)
        return _FakeStreamResponse(app_zip_bytes)

    monkeypatch.setattr(httpx, "stream", fake_stream)

    progress_calls = []
    staging_dir = tmp_path / "staging"
    stage_update(result, staging_dir, on_progress=lambda *args: progress_calls.append(args))

    assert (staging_dir / "atlas-desktop.exe").read_bytes() == b"exe"
    # The real vendor zip's own root is bare "playwright/" (it's zipped
    # from inside the build's _internal/ dir), but the real install's
    # playwright/ lives at _internal/playwright/ — extracting straight
    # into staging_dir would misplace it as a sibling of _internal/
    # instead of inside it, which robocopy would then silently never
    # merge into the real _internal/playwright/ at all.
    assert (staging_dir / "_internal" / "playwright" / "driver" / "node.exe").read_bytes() == b"node"
    assert not (staging_dir / "playwright").exists()
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
