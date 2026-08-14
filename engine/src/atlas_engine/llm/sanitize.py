import re

# Plain-ASCII sentinel: won't appear in real nav labels, contains no "<", and
# survives the blanket redaction below via that pass's explicit guard.
KEEP_OPEN = "%%K"
KEEP_CLOSE = "K%%"
LABEL_CAP = 100

CONTROL_TAG = re.compile(r"^(?:a|button|summary|option|label)$", re.IGNORECASE)
CONTROL_ROLE = re.compile(
    r'\brole="(?:option|menuitem|menuitemcheckbox|menuitemradio|tab|treeitem|link|button|checkbox|radio|switch)"',
    re.IGNORECASE,
)
LABEL_CLASS = re.compile(
    r'\bclass="[^"]*(?:bx--link|[\w-]+-link|truncatedText|menuItem|listItem|ba-common-label|nav-link|dropdown-item)[^"]*"',
    re.IGNORECASE,
)

DEFAULT_WINDOW_CHARS = 1500
DEFAULT_MAX_MATCHES = 40
DEFAULT_MAX_TOTAL_CHARS = 60_000


def hint_tokens_from_selectors(
    selectors: list[dict] | list[str],
    extra: list[str] | None = None,
) -> list[str]:
    """Build hint tokens that actually occur in the HTML from a step's Playwright
    selectors (+ optional extra strings like the step label). Extracting the
    identifier fragments (ids, classes, quoted has-text/name values, attribute
    values) gives tokens that DO appear, so the context windows land on the
    right region. Matches legacy hintTokensFromSelectors."""
    out: list[str] = []

    def push(t: str | None) -> None:
        if t and len(t.strip()) >= 3:
            out.append(t.strip())

    for s in selectors:
        if isinstance(s, dict):
            v = s.get("selector") or s.get("value") or ""
        else:
            v = str(s)

        # role=<role> name=<accessible name that may contain spaces>
        role_name = re.search(r"^role=\S+\s+name=(.+)$", v)
        if role_name:
            push(role_name.group(1))

        for m in re.finditer(r'"([^"]+)"', v):
            push(m.group(1))
        for m in re.finditer(r"'([^']+)'", v):
            push(m.group(1))

        name_eq = re.search(r"\bname=([^\s\"'\]]+)", v)
        if name_eq:
            push(name_eq.group(1))

        for m in re.finditer(r"#([A-Za-z][\w-]{2,})", v):
            push(m.group(1))
        for m in re.finditer(r"\.([A-Za-z][\w-]{2,})", v):
            push(m.group(1))
        for m in re.finditer(r"\[[\w-]+[~|^$*]?=[\"']?([^\"'\]]+)[\"']?\]", v):
            push(m.group(1))

    for e in extra or []:
        push(e)

    # Dedup, preserve first-seen order
    seen = set()
    result = []
    for item in out:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def hint_tokens_for_step(step) -> list[str]:
    """Extracts hint tokens from a TrajectoryStep matching legacy hintTokens(entry)."""
    toks: list[str] = []
    candidates = getattr(step, "selector_candidates", []) or []
    for cand in candidates:
        if isinstance(cand, dict):
            s = cand.get("selector") or ""
        else:
            s = str(cand)
        for part in re.split(r"\W+", s):
            if len(part) >= 3:
                toks.append(part)

    identity = getattr(step, "identity", None)
    if isinstance(identity, dict):
        if identity.get("id"):
            toks.append(identity["id"])
        if identity.get("text"):
            toks.extend(re.split(r"\W+", identity["text"]))

    seen = set()
    result = []
    for t in toks:
        if len(t) >= 3 and t not in seen:
            seen.add(t)
            result.append(t)
    return result[:24]


def is_control_or_label_tag(open_tag: str) -> bool:
    m = re.match(r"^<([a-zA-Z][\w-]*)", open_tag)
    tag_name = m.group(1) if m else ""
    return bool(
        CONTROL_TAG.match(tag_name)
        or CONTROL_ROLE.search(open_tag)
        or LABEL_CLASS.search(open_tag)
    )


def redact_text_nodes(fragment: str) -> str:
    """Redact inter-tag text content and long attribute values; keep all
    tags/attributes/ids/classes intact. Preserves direct text of interactive
    controls (buttons, links, labels) up to 100 chars."""
    preserved: list[str] = []

    preserve_re = re.compile(
        rf"(<[a-zA-Z][\w-]*\b[^>]*>)\s*([^<>]{{1,{LABEL_CAP}}}?)\s*(?=<)"
    )

    def _preserve_repl(match: re.Match) -> str:
        open_tag = match.group(1)
        text = match.group(2)
        if not text.strip() or KEEP_OPEN in text:
            return match.group(0)
        if not is_control_or_label_tag(open_tag):
            return match.group(0)
        token = f"{KEEP_OPEN}{len(preserved)}{KEEP_CLOSE}"
        preserved.append(text.strip())
        return open_tag + token

    marked = preserve_re.sub(_preserve_repl, fragment)

    def _blank_inter_tag(match: re.Match) -> str:
        text = match.group(1)
        if not text.strip() or KEEP_OPEN in text:
            return match.group(0)
        return ">…<"

    marked = re.sub(r">([^<]{4,})<", _blank_inter_tag, marked)
    marked = re.sub(r'(\bvalue=)"[^"]{6,}"', r'\1"…"', marked, flags=re.IGNORECASE)
    marked = re.sub(r'(\btitle=)"[^"]{60,}"', r'\1"…"', marked, flags=re.IGNORECASE)
    marked = re.sub(r'(\baria-label=)"[^"]{80,}"', r'\1"…"', marked, flags=re.IGNORECASE)

    def _restore_preserved(match: re.Match) -> str:
        idx = int(match.group(1))
        return preserved[idx] if idx < len(preserved) else "…"

    return re.sub(rf"{KEEP_OPEN}(\d+){KEEP_CLOSE}", _restore_preserved, marked)


def sanitize_html_for_diagnosis(
    html: str,
    hint_tokens: list[str] | None = None,
    window_chars: int = DEFAULT_WINDOW_CHARS,
    max_matches: int = DEFAULT_MAX_MATCHES,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
) -> str:
    """Sanitize HTML for LLM diagnosis, extracting context windows around hint
    tokens and preserving control labels (up to 100 chars) while redacting PHI.
    A verbatim port of legacy sanitizeHtmlForDiagnosis."""
    cleaned = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.IGNORECASE)
    cleaned = re.sub(r"<style[\s\S]*?</style>", "", cleaned, flags=re.IGNORECASE)

    tokens = hint_tokens or []
    windows: list[dict] = []
    total_matches = 0
    for token in tokens:
        if not token or total_matches >= max_matches:
            break
        escaped = re.escape(token)
        for m in re.finditer(escaped, cleaned, flags=re.IGNORECASE):
            if total_matches >= max_matches:
                break
            windows.append({
                "start": max(0, m.start() - window_chars // 2),
                "end": min(len(cleaned), m.start() + len(token) + window_chars // 2),
            })
            total_matches += 1

    windows.sort(key=lambda w: w["start"])
    merged: list[dict] = []
    for w in windows:
        if merged and w["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], w["end"])
        else:
            merged.append(dict(w))

    if merged:
        slices = [cleaned[w["start"]:w["end"]] for w in merged]
    else:
        slices = [cleaned[:max_total_chars]]

    redacted = [redact_text_nodes(s) for s in slices]
    joined = "\n\n<!-- ... -->\n\n".join(redacted)
    if len(joined) > max_total_chars:
        joined = joined[:max_total_chars] + "\n<!-- ...[truncated] -->"
    return joined


def sanitize_dom(html: str) -> str:
    """Fast blanket DOM sanitizer (backwards compatibility)."""
    return sanitize_html_for_diagnosis(html)


def sanitize_text_value(value: str) -> str:
    if value == "":
        return ""
    return "<REDACTED>"

