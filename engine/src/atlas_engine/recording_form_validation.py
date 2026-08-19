import re

# Recording form v1 (docs/superpowers/specs/2026-08-19-recording-form-v1-design.md)
# — Section B business rules from atlas-recording-form-v1.md's
# "Validation" implementation note. Pure functions: no DB access, so the
# API layer can call these before touching the service layer.

_ABSOLUTE_DATE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),  # 2026-01-01
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),  # 1/1/2026
    re.compile(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(st|nd|rd|th)?\b",
        re.IGNORECASE,
    ),  # "Jan 1", "January 1st"
    re.compile(r"\b\d{4}\b"),  # any bare 4-digit year
)


def validate_payers_required(payer_scope: str | None, payer_ids: list | None) -> None:
    if payer_scope is not None and payer_scope != "All payers" and not payer_ids:
        raise ValueError("payers is required unless payer_scope is 'All payers'")


def validate_sub_reason_required(sub_reason: str | None, carc_code_is_multi_use: bool) -> None:
    if carc_code_is_multi_use and not sub_reason:
        raise ValueError("sub_reason is required for a multi-use code")


def validate_date_param_logic(date_param_logic: str | None) -> None:
    """Rejects absolute dates — date_param_logic must describe a date
    range relative to the run date (e.g. "prior calendar month")."""
    if date_param_logic is None:
        return
    for pattern in _ABSOLUTE_DATE_PATTERNS:
        if pattern.search(date_param_logic):
            raise ValueError(f"date_param_logic must be relative to the run date, not an absolute date: {date_param_logic!r}")


def validate_post_processing(post_processing: str | None) -> None:
    """Requires real text or the literal "None" — silently blank is not
    an acceptable answer to "was there manual work after the download."""
    if post_processing is not None and post_processing.strip() == "":
        raise ValueError('post_processing requires text or the literal "None"')
