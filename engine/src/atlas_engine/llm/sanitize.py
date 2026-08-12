import re

_TAG_TEXT_RE = re.compile(r">([^<>]+)<", re.MULTILINE)
# Attributes that carry user/PHI-bearing content rather than structure —
# blanked out; id/class/data-testid/type/name/href etc. are left alone since
# the resolver needs those to find elements.
_VALUE_ATTR_RE = re.compile(
    r'\b(value|placeholder|title|alt)=(["\'])(.*?)\2', re.MULTILINE | re.DOTALL
)


def sanitize_dom(html: str) -> str:
    """Strip inter-tag text content and value-bearing attributes while
    keeping tags, structural attributes, and nesting intact — an LLM
    diagnosing a broken selector needs the shape of the page, never the
    PHI-bearing values rendered on it."""

    def _blank_text(match: re.Match) -> str:
        text = match.group(1)
        if text.strip() == "":
            return match.group(0)
        return ">" + " " * len(text) + "<"

    def _blank_attr(match: re.Match) -> str:
        name, quote, value = match.group(1), match.group(2), match.group(3)
        return f"{name}={quote}{' ' * len(value)}{quote}"

    without_attr_values = _VALUE_ATTR_RE.sub(_blank_attr, html)
    return _TAG_TEXT_RE.sub(_blank_text, without_attr_values)


def sanitize_text_value(value: str) -> str:
    if value == "":
        return ""
    return "<REDACTED>"
