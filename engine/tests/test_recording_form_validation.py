import pytest

from atlas_engine.recording_form_validation import (
    validate_date_param_logic,
    validate_payers_required,
    validate_post_processing,
    validate_sub_reason_required,
)


def test_validate_payers_required_passes_when_all_payers():
    validate_payers_required("All payers", None)  # must not raise


def test_validate_payers_required_raises_when_missing_for_specific_scope():
    with pytest.raises(ValueError, match="payers is required"):
        validate_payers_required("Payer-specific", None)


def test_validate_payers_required_passes_when_provided():
    validate_payers_required("Payer-specific", ["payer-1"])  # must not raise


def test_validate_sub_reason_required_raises_for_multi_use_code_without_sub_reason():
    with pytest.raises(ValueError, match="sub_reason is required"):
        validate_sub_reason_required(None, True)


def test_validate_sub_reason_required_passes_for_non_multi_use_code():
    validate_sub_reason_required(None, False)  # must not raise


def test_validate_sub_reason_required_passes_when_provided():
    validate_sub_reason_required("Missing NDC", True)  # must not raise


@pytest.mark.parametrize("value", [
    "2026-01-01", "1/1/2026", "Jan 1", "January 1st", "prior 2026 activity",
])
def test_validate_date_param_logic_rejects_absolute_dates(value):
    with pytest.raises(ValueError, match="relative to the run date"):
        validate_date_param_logic(value)


@pytest.mark.parametrize("value", [None, "prior calendar month", "last 7 days"])
def test_validate_date_param_logic_accepts_relative_phrasing(value):
    validate_date_param_logic(value)  # must not raise


def test_validate_post_processing_rejects_blank_string():
    with pytest.raises(ValueError, match="requires text"):
        validate_post_processing("   ")


@pytest.mark.parametrize("value", [None, "None", "Re-key into billing system"])
def test_validate_post_processing_accepts_none_or_real_text(value):
    validate_post_processing(value)  # must not raise
