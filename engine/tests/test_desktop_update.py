import httpx
import pytest

from atlas_engine.desktop_update import (
    UpdateCheckResult,
    check_for_update,
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
