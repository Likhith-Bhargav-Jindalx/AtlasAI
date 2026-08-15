import ast
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import BrowserContext

logger = logging.getLogger(__name__)

# The action methods Playwright's codegen recorder can emit for a locator or
# page. `goto` is handled separately (it's a navigation, not a page method).
_ACTION_METHODS = {
    "click",
    "dblclick",
    "fill",
    "hover",
    "press",
    "select_option",
    "type",
    "check",
    "uncheck",
}

# Locator methods that carry a typed/selected value as their first argument.
_VALUE_METHODS = {"fill", "press", "type", "select_option"}

@dataclass
class CodegenAction:
    """One action line parsed from a generated Playwright script. `goto`'s
    selector is the target URL; every other kind's selector is the locator
    expression the recorder emitted. `page_var` is the page root of the
    expression (`page`, `page1`, ...) so a popup action can be replayed on
    the right Playwright page."""
    kind: str
    selector: str
    value: str | None = None
    page_var: str = "page"
    page_index: int = 0
    expects_popup: bool = False
    expects_download: bool = False


def attach_codegen_recorder(context: BrowserContext, output_path: Path) -> None:
    """Enables Playwright's native codegen recorder, writing Python to
    `output_path`. This is the single place this codebase touches the
    private `enableRecorder` channel; any failure logs a warning and the
    recording continues without a script — this feature is best-effort."""
    try:
        context._sync(
            context._impl_obj._channel.send(
                "enableRecorder",
                None,
                {"language": "python", "mode": "recording", "outputFile": str(output_path)},
            )
        )
    except Exception:
        logger.warning(
            "Native codegen recorder unavailable; recording continues without a script",
            exc_info=True,
        )


def strip_extraction_markers(codegen: str) -> str:
    """Replaces the extraction button's click line with a positional
    marker comment (the button itself never exists outside the record
    session, so its own click can't be replayed) — kept in place, not
    deleted, so parse_codegen_actions can turn it into a real "extract"
    step at the same point in the flow."""
    kept = []
    for line in codegen.splitlines():
        if "__atlasGrabBtn" in line or "Extract this page" in line:
            kept.append("# __ATLAS_EXTRACT__")
        else:
            kept.append(line)
    return "\n".join(kept)


def _unquote(arg: str) -> str:
    """Turns a Python string literal from the codegen into its value,
    falling back to the raw text when it isn't a literal."""
    try:
        return ast.literal_eval(arg)
    except Exception:
        return arg.strip().strip("\"'")


def _first_and_second_args(args: str) -> tuple[str, str | None]:
    """Splits a codegen argument list on the top-level comma and unquotes
    the first two arguments. Anything beyond the second is dropped."""
    parts = []
    depth = 0
    current = []
    for ch in args:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    selector = _unquote(parts[0]) if parts else ""
    value = _unquote(parts[1]) if len(parts) > 1 else None
    return selector, value


def _split_action_call(line: str) -> tuple[str, str, str] | None:
    """Splits a codegen action line into (chain, method, args) using balanced
    parentheses — the chain is everything before the LAST top-level
    `.method(` (the full locator expression, INCLUDING its page root), and
    args is the balanced contents of that final call. A greedy regex can't
    do this: `page.locator("#x").get_by_role("button").filter(has_text=...).click()`
    must keep the WHOLE chain, not just the first `locator(...)` argument."""
    open_idx: int | None = None
    depth = 0
    for i in range(len(line) - 1, -1, -1):
        ch = line[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            depth -= 1
            if depth == 0:
                open_idx = i
                break
    if open_idx is None:
        return None
    args_str = line[open_idx + 1 : len(line) - 1] if line.endswith(")") else line[open_idx + 1 :]
    depth = 0
    for j in range(open_idx - 1, -1, -1):
        ch = line[j]
        if ch == ")":
            depth += 1
        elif ch == "(":
            depth -= 1
        elif ch == "." and depth == 0:
            method = line[j + 1 : open_idx]
            if re.fullmatch(r"[A-Za-z_]\w*", method):
                return line[:j], method, args_str
    return None


def _page_var_from_chain(chain: str) -> str:
    m = re.match(r"^(\w+)\.", chain)
    return m.group(1) if m else "page"


def _page_index_of(page_var: str) -> int:
    m = re.fullmatch(r"page(\d*)", page_var)
    if not m:
        return 0
    return int(m.group(1)) if m.group(1) else 0


def parse_codegen_actions(codegen: str) -> list[CodegenAction]:
    """Classifies the action lines of a generated Playwright script into
    structured CodegenActions. `expect(...)` assertions, comments,
    `page.close()`/`context.close()`, `page.once(...)` and other structural
    lines are skipped. Locator chains — `locator(...)`, `get_by_*`,
    `.filter(...)`, `.content_frame`, `.nth(...)` — are preserved verbatim
    (with the `codegen_expr:` marker) so replay can re-evaluate them as real
    Playwright locators, and popup pages (`page1`, ...) are tracked."""
    actions: list[CodegenAction] = []
    # Active `with page.expect_popup(...)` / `expect_download(...)` blocks,
    # tracked by indentation so the action INSIDE the block is marked as the
    # popup/download trigger and the blocks pop off when the codegen dedents.
    expect_stack: list[tuple[int, str]] = []
    for raw in codegen.splitlines():
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        if line == "# __ATLAS_EXTRACT__":
            actions.append(CodegenAction(kind="extract", selector=""))
            continue
        if not line or line.startswith("#"):
            continue

        with_block = re.match(r"^with \w+\.(expect_popup|expect_download)\(\)", line)
        if with_block:
            expect_stack.append((indent, "popup" if with_block.group(1) == "expect_popup" else "download"))
            continue

        while expect_stack and expect_stack[-1][0] >= indent:
            expect_stack.pop()
        expects_popup = any(kind == "popup" for _, kind in expect_stack)
        expects_download = any(kind == "download" for _, kind in expect_stack)

        if line.startswith("expect("):
            continue

        goto = re.match(r"^(\w+)\.goto\((.*)\)$", line)
        if goto and line.endswith(")"):
            page_var = goto.group(1)
            url, _ = _first_and_second_args(goto.group(2))
            actions.append(
                CodegenAction(
                    kind="goto", selector=url, page_var=page_var,
                    page_index=_page_index_of(page_var),
                    expects_popup=expects_popup, expects_download=expects_download,
                )
            )
            continue

        split = _split_action_call(line)
        if split is None:
            continue
        chain, method, args = split
        if method not in _ACTION_METHODS:
            continue
        page_var = _page_var_from_chain(chain)
        first, second = _first_and_second_args(args)
        if chain == page_var:
            # A bare `page.click("#x")` / `page.fill("#x", value)` — the first
            # argument is a CSS selector, the second (if any) the typed value.
            selector = first
            value = second if method in _VALUE_METHODS else None
        else:
            # Everything after the page root is a locator chain that must be
            # re-evaluated as a Playwright locator at replay time; a
            # value-method's typed value is its first argument.
            selector = "codegen_expr:" + chain
            value = first if method in _VALUE_METHODS else None
        actions.append(
            CodegenAction(
                kind=method, selector=selector, value=value,
                page_var=page_var, page_index=_page_index_of(page_var),
                expects_popup=expects_popup, expects_download=expects_download,
            )
        )
    return actions


# Kinds that produce replay steps (kept faithful to the recorded gesture).
# `goto` is deliberately EXCLUDED — see build_codegen_trajectory's docstring.
_TRAJECTORY_KINDS = {
    "click",
    "dblclick",
    "fill",
    "hover",
    "press",
    "select_option",
    "type",
    "check",
    "uncheck",
    "extract",
}


def build_codegen_trajectory(codegen_actions: list[CodegenAction]) -> ReferenceTrajectory | None:
    """Builds a ReferenceTrajectory directly from the recorded codegen script
    — the script is the ground truth of what the user did, and (unlike the
    raw recorder's CSS candidates) it covers navigations, popups and iframes.
    `goto` actions are dropped entirely: a goto in a codegen script is a page
    (re)load the recorder observed as a SIDE EFFECT of a click — not an
    independent action — and this app's own orchestrator already performs
    the flow's one real entry navigation before replay starts (using the
    portal's inferred/configured login URL). Replaying a recorded goto as a
    standalone page.goto() re-issues that URL as a fresh, stateless request,
    discarding whatever session/flow state the click's own navigation
    carried — which can bounce a multi-page login flow (username page ->
    password page -> dashboard) straight back to its first page. Matches the
    previous app's codegenImport.ts::assemble() exactly: "a page.goto(...)
    line is a re-load the recorder captured, not a real step." Effects are
    conservative so a faithful replay doesn't pause spuriously: clicks/fills
    pass once the action executes (the next step fails loudly if a step's
    effect was actually wrong)."""
    from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep

    steps = []
    for i, action in enumerate(codegen_actions):
        if action.kind not in _TRAJECTORY_KINDS:
            continue
        effect = "none"
        candidates = [] if action.kind == "extract" else [{"selector": action.selector, "rank": 0}]
        steps.append(
            TrajectoryStep(
                index=len(steps),
                kind=action.kind,
                selector_candidates=candidates,
                value=action.value,
                expected_effect=effect,
                expects_popup=action.expects_popup,
                expects_download=action.expects_download,
                wait_ceiling_ms=60000,
            )
        )
    if not steps:
        return None
    return ReferenceTrajectory(steps=tuple(steps))