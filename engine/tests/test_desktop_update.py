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
