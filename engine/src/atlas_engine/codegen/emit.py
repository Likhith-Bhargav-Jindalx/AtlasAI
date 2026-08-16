import os
import re
import sys
import textwrap

from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.capture.extraction import SERIALIZER as _SERIALIZER_JS
from atlas_engine.codegen.params import ClassifiedParams, classify_step_values

# ── Emitted helper templates ────────────────────────────────────────────────
# Each is spliced into the generated script's header. Emits self-contained
# deterministic helpers with zero atlas_engine runtime dependencies.

_DUMP_FAILURE_CONTEXT_HELPER = '''
def dump_failure_context(target, page, step_id, label, error, diagnostics_dir):
    os.makedirs(diagnostics_dir, exist_ok=True)
    try:
        html = target.content()
    except Exception:
        html = ""
    screenshot_path = os.path.join(diagnostics_dir, f"step_{step_id}_failure.png")
    try:
        page.screenshot(path=screenshot_path)
    except Exception:
        screenshot_path = None
    diag = {
        "stepId": step_id,
        "label": label,
        "error": error,
        "url": page.url,
        "html": html,
        "screenshot": screenshot_path,
    }
    with open(os.path.join(diagnostics_dir, f"step_{step_id}_failure.json"), "w") as f:
        json.dump(diag, f)
    log_step(f"Failure captured for step {step_id} -> {diagnostics_dir}/step_{step_id}_failure.json")
'''

_SETTLE_FRAMES_HELPER = '''
def _settle_frames(pg, timeout_ms=15000):
    # Bounded wait for the iframe tree to stop changing — nested frames (Epic
    # reloads/attaches them on navigation) must finish loading before we act,
    # or a click/wait lands on an about:blank frame and times out. Signal = the
    # set of frame URLs unchanged for ~1.5s. Fast when idle; never hangs.
    import time as _t
    deadline = _t.time() + timeout_ms / 1000.0
    prev = None; stable = 0
    while _t.time() < deadline:
        try:
            sig = tuple(sorted((f.url or '') for f in pg.frames))
        except Exception:
            return
        if sig == prev:
            stable += 1
            if stable >= 3: return
        else:
            stable = 0
        prev = sig
        try: pg.wait_for_timeout(500)
        except Exception: return
'''

_FIRST_VISIBLE_HELPER = '''
def first_visible_selector(page, hints, per_try_ms):
    for sel in hints:
        try:
            page.locator(sel).first.wait_for(state="visible", timeout=per_try_ms)
            return sel
        except Exception:
            continue
    raise Exception("None of the selectors matched: " + ", ".join(hints))
'''

_HEADER_TOP = '''import argparse
import json
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright


def log_step(msg):
    print(f"[step] {msg}", file=sys.stderr)


def _page(page, index):
    """Resolves the Nth page of the shared browser context: page 0 is the
    page the run started on; pages 1+ are popups the app opened."""
    if index == 0:
        return page
    deadline = time.time() + 15
    while time.time() < deadline:
        pages = page.context.pages
        if len(pages) > index:
            return pages[index]
        page.wait_for_timeout(200)
    raise TimeoutError(f"Popup page {index} never opened")
'''

_PACE_HELPER_TEMPLATE = '''

PACING_MS = {pre_action_ms}
def _pace(pg):
    # Slow-portal pacing: human-like pause before each action (config portal.pacing).
    try:
        pg.wait_for_timeout(PACING_MS)
    except Exception:
        pass
'''

_CODEGEN_EXPR_PREFIX = "codegen_expr:"
_PAGE_ROOT_RE = re.compile(r"^(page\d*)\.(.*)", re.DOTALL)

_FIND_FRAME_WITH_ELEMENT_HELPER = '''
def find_frame_with_element(pg, selector, timeout_ms=30000, host=None, prefer_child=False):
    # Searches every frame of `pg` for one matching `selector`, polling until
    # found or timeout. Excludes the main page so a generic selector never
    # accidentally matches against the top-level document.
    def rank(fr):
        r = 0
        if prefer_child and (fr == pg.main_frame or fr is pg.main_frame):
            r += 100
        if host and host not in (fr.url or ""):
            r += 10
        return r
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for frame in sorted(pg.frames, key=rank):
            if (prefer_child or selector == "*") and (frame == pg.main_frame or frame is pg.main_frame):
                continue
            try:
                if selector == "*":
                    return frame
                el = frame.query_selector(selector)
                if el is not None:
                    return frame
            except Exception:
                pass
        pg.wait_for_timeout(500)
    raise Exception(f"No frame found containing: {selector} (waited {timeout_ms // 1000}s)")
'''

_IFRAME_CHAIN_RE = re.compile(r'^(.*?)\.locator\("iframe\[name=\\?"[^"]+\\?"\]"\)\.content_frame\.(.*)$')

_FRAME_PROBE_RE = re.compile(r'get_by_role\(\s*["\']([^"\']+)["\']')
_FRAME_PROBE_LOCATOR_RE = re.compile(r'locator\(\s*["\']([^"\']+)["\']')
_FRAME_PROBE_LABEL_RE = re.compile(r'get_by_label\(\s*["\']([^"\']+)["\']')

_PRINT_CAPTURE_INIT_SCRIPT = (
    "(() => {\n"
    "  if (window.__atlasPrintHooked) return;\n"
    "  window.__atlasPrintHooked = true;\n"
    "  var serialize = function () {\n"
    "    var styles = '';\n"
    "    var sheets = document.styleSheets || [];\n"
    "    for (var i = 0; i < sheets.length; i++) {\n"
    "      try { var rules = sheets[i].cssRules; if (rules) { for (var j = 0; j < rules.length; j++) styles += rules[j].cssText + ' '; } }\n"
    "      catch (e) {}\n"
    "    }\n"
    "    var inner = document.body ? document.body.innerHTML : document.documentElement.outerHTML;\n"
    "    return { styles: styles, inner: inner, base: location.href, title: (document.title || 'report') };\n"
    "  };\n"
    "  var fire = function () { try { if (window.__atlasOnPrint) window.__atlasOnPrint(serialize()); } catch (e) {} };\n"
    "  try { window.print = fire; } catch (e) {}\n"
    "})();"
)

_PRINT_CAPTURE_HELPER = '''
_PRINT_CAPTURES = []

def _atlas_sanitize(name):
    keep = ''.join((c if (c.isalnum() or c in ' -_') else '_') for c in (name or 'report')).strip()
    return (keep or 'report')[:80]

def _atlas_on_print(source, data=None):
    try:
        if data and data.get("inner"):
            _PRINT_CAPTURES.append(data)
            try: print("[print] captured a window.print() -> queued for PDF", file=sys.stderr)
            except Exception: pass
    except Exception as _pe:
        try: print("[print] capture failed: " + str(_pe)[:160], file=sys.stderr)
        except Exception: pass
    return True

def _flush_print_pdfs(playwright, out_dir, prefix=""):
    saved = []
    if not _PRINT_CAPTURES:
        return saved
    try: os.makedirs(out_dir, exist_ok=True)
    except Exception: pass
    for _i, _d in enumerate(_PRINT_CAPTURES):
        _hb = None
        try:
            _base = _d.get("base") or ""
            _head = "<!doctype html><html><head><meta charset='utf-8'>"
            if _base: _head += "<base href='" + _base + "'>"
            _head += "<style>" + (_d.get("styles") or "") + "</style></head><body>"
            _full = _head + (_d.get("inner") or "") + "</body></html>"
            _hb = playwright.chromium.launch(headless=True)
            _hp = _hb.new_context().new_page()
            try: _hp.set_content(_full, wait_until="load", timeout=15000)
            except Exception:
                try: _hp.set_content(_full)
                except Exception: pass
            name = prefix + _atlas_sanitize(_d.get("title")) + (".pdf" if _i == 0 else ("_" + str(_i + 1) + ".pdf"))
            dest = os.path.join(out_dir, name)
            _hp.pdf(path=dest, format="A4", print_background=True)
            saved.append({"filename": name, "path": dest})
            try: print("[print] saved PDF: " + name, file=sys.stderr)
            except Exception: pass
        except Exception as _fe:
            try: print("[print] PDF render failed: " + str(_fe)[:160], file=sys.stderr)
            except Exception: pass
        finally:
            try:
                if _hb is not None: _hb.close()
            except Exception: pass
    _PRINT_CAPTURES.clear()
    return saved
'''


def _uses_static_iframe_reference(expr: str) -> bool:
    """True when a codegen locator chain traverses into an iframe via a
    baked-in iframe[name="..."] selector."""
    return bool(_IFRAME_CHAIN_RE.search(expr))


def _frame_probe_from_rest(rest: str) -> str:
    """Derives a query_selector-able probe from a codegen locator chain's
    trailing expression (the part after `.content_frame.`). The old app
    matched frames by the step's OWN target locator (e.g. `role=textbox`
    from a `get_by_role("textbox")` chain); the equivalent here must also
    probe with something real, never a catch-all '*' — otherwise the first
    child frame (toolbar chrome, adverts, etc.) wins regardless of whether
    it actually contains the target element."""
    m = _FRAME_PROBE_RE.search(rest)
    if m:
        return f"role={m.group(1)}"
    m = _FRAME_PROBE_LABEL_RE.search(rest)
    if m:
        return f"label={m.group(1)}"
    m = _FRAME_PROBE_LOCATOR_RE.search(rest)
    if m:
        return m.group(1)
    return "*"


def _emit_dynamic_frame_step(expr: str, step: TrajectoryStep, *, value_expr: str | None = None) -> list[str]:
    """Replaces a static iframe[name=...].content_frame reference with a
    runtime search over every live frame for one containing the step's OWN
    target locator."""
    m = _IFRAME_CHAIN_RE.match(expr)
    root_expr, rest = m.group(1), m.group(2)
    m_root = _PAGE_ROOT_RE.match(root_expr + ".")
    page_expr = f"_page(page, {int(m_root.group(1)[4:])})" if m_root and m_root.group(1) != "page" else "page"
    probe = _frame_probe_from_rest(rest)
    lines = [f"_frame = find_frame_with_element({page_expr}, {probe!r}, prefer_child=True)"]
    lines.extend(g.lstrip() for g in _emit_locator_gesture(f"_frame.{rest}", step, value_expr=value_expr))
    return lines


def _build_review_header(
    trajectory: ReferenceTrajectory, *, portal_name: str, confidence: float | None,
    classified: "ClassifiedParams",
) -> str:
    """Banner comment matching the previous app's Stage-5 review header."""
    warnings = []
    no_value_indices = [p.step_index for p in classified.params if not p.default_value]
    if no_value_indices:
        ids = ", ".join(str(i) for i in no_value_indices)
        warnings.append(
            f"#  - {len(no_value_indices)} fill step(s) have no captured/parameterized value "
            f"(PHI redaction): {ids}"
        )
    if any(s.expects_popup for s in trajectory.steps):
        warnings.append(
            "#  - This flow opens at least one new page/popup — the trigger step is a judgment call, not a certainty."
        )
    confidence_str = f"{confidence}" if confidence is not None else "unknown"
    lines = [
        "# ==============================================================================",
        f"# GENERATED by Atlas AI — portal: {portal_name}",
        f"# Source trajectory confidence: {confidence_str}",
    ]
    if warnings:
        lines.append("#")
        lines.append("# REVIEW BEFORE TRUSTING RESULTS:")
        lines.extend(warnings)
    lines.append("# ==============================================================================")
    return "\n".join(lines) + "\n"


_NTH_SUFFIX_RE = re.compile(r"^(.*?)(\s*>>\s*nth=\d+)\s*$")


def _prefer_visible(selector: str, force_click: bool) -> str:
    """Mirror the brain's runtime behavior ('act on the VISIBLE match') in
    the static golden."""
    if force_click:
        return selector
    if "visible=true" in selector:
        return selector
    if selector.startswith(_CODEGEN_EXPR_PREFIX):
        return selector
    m = _NTH_SUFFIX_RE.match(selector)
    if m:
        return f"{m.group(1)} >> visible=true{m.group(2)}"
    return f"{selector} >> visible=true"


def _looks_session_random(id_value: str) -> bool:
    """Ported from emitGolden.ts looksSessionRandom()."""
    if re.search(r"\d{3,}", id_value):
        return True
    tail = id_value.rsplit("_", 1)[-1].rsplit("-", 1)[-1]
    return len(tail) >= 6 and bool(re.search(r"\d", tail)) and bool(re.fullmatch(r"[a-zA-Z0-9]+", tail))


def _best_selector(step: TrajectoryStep) -> str:
    """Pick the best selector from a step's candidates, skipping session-random
    IDs and applying visible=true for CSS selectors."""
    ranked = sorted(step.selector_candidates, key=lambda c: c["rank"])
    for cand in ranked:
        sel = cand["selector"]
        if sel.startswith("#") and _looks_session_random(sel.lstrip("#")):
            continue
        return sel
    return ranked[0]["selector"]


def _emit_locator_gesture(call: str, step: TrajectoryStep, *, value_expr: str | None = None) -> list[str]:
    """Per-gesture lines for an already-resolved Playwright receiver chain `call`."""
    if step.kind == "click":
        return [f"{call}.click(force=True)" if step.force_click else f"{call}.click()"]
    if step.kind == "dblclick":
        return [f"{call}.dblclick()"]
    if step.kind == "hover":
        return [f"{call}.hover()", "page.wait_for_timeout(900)"]
    if step.kind == "hover_click":
        return [f"{call}.hover()", "page.wait_for_timeout(900)", f"{call}.click()"]
    if step.kind == "dispatch_click":
        return [f'{call}.dispatch_event("click")']
    if step.kind == "fill":
        return [f"{call}.fill({value_expr})" if value_expr is not None else f"{call}.fill({step.value!r})"]
    if step.kind == "type":
        seq = value_expr if value_expr is not None else repr(step.value or "")
        return [f"{call}.click()", f"{call}.fill('')", f"{call}.press_sequentially({seq}, delay=100)"]
    if step.kind == "press":
        return [f"{call}.press({(step.key or step.value or 'Enter')!r})"]
    if step.kind == "check":
        return [f"{call}.check()"]
    if step.kind == "uncheck":
        return [f"{call}.uncheck()"]
    raise ValueError(f"Unsupported step kind for emission: {step.kind}")


def _emit_locator_call(
    expr: str, step: TrajectoryStep, *, pre_action_ms: int = 0, settle_after_nav_ms: int = 0,
    value_expr: str | None = None,
) -> list[str]:
    """Emits Playwright method calls for a codegen_expr selector."""
    lines: list[str] = []
    if _uses_static_iframe_reference(expr):
        return _emit_dynamic_frame_step(expr, step, value_expr=value_expr)

    m = _PAGE_ROOT_RE.match(expr)
    if not m:
        root_expr, rest = "page", expr
    else:
        root_expr, rest = m.group(1), m.group(2)

    page_idx = int(root_expr[4:]) if root_expr != "page" else 0
    page_target = f"_page(page, {page_idx})" if page_idx > 0 else "page"
    receiver = f"{page_target}.{rest}"

    if pre_action_ms > 0:
        lines.append(f"    _pace({page_target})")

    lines.extend(f"    {g}" for g in _emit_locator_gesture(receiver, step, value_expr=value_expr))

    if step.expected_effect == "url_changed":
        lines.append(f"    {page_target}.wait_for_load_state()")
    return lines


def _emit_css_step(
    step: TrajectoryStep, selector: str, *, pre_action_ms: int = 0, settle_after_nav_ms: int = 0,
    value_expr: str | None = None,
) -> list[str]:
    """Emits calls for a step with a plain CSS selector."""
    lines: list[str] = []
    if pre_action_ms > 0:
        lines.append("    _pace(page)")

    selector = _prefer_visible(selector, step.force_click)
    use_locator = ">>" in selector or not step.selector_candidates

    if step.kind == "click":
        if use_locator:
            click_call = ".click(force=True)" if step.force_click else ".click()"
            lines.append(f"    page.locator({selector!r}).first{click_call}")
        else:
            lines.append(f"    page.click({selector!r}, force=True)" if step.force_click else f"    page.click({selector!r})")
    elif step.kind == "dblclick":
        lines.append(f"    page.locator({selector!r}).first.dblclick()" if use_locator else f"    page.dblclick({selector!r})")
    elif step.kind == "hover":
        lines.append(f"    page.locator({selector!r}).first.hover()" if use_locator else f"    page.hover({selector!r})")
        lines.append("    page.wait_for_timeout(900)")
    elif step.kind == "hover_click":
        lines.append(f"    page.locator({selector!r}).first.hover()" if use_locator else f"    page.hover({selector!r})")
        lines.append("    page.wait_for_timeout(900)")
        lines.append(f"    page.locator({selector!r}).first.click()" if use_locator else f"    page.click({selector!r})")
    elif step.kind == "dispatch_click":
        lines.append(f'    page.locator({selector!r}).first.dispatch_event("click")')
    elif step.kind == "fill":
        val = value_expr if value_expr is not None else repr(step.value or "")
        if use_locator:
            lines.append(f"    page.locator({selector!r}).first.fill({val})")
        else:
            lines.append(f"    page.fill({selector!r}, {val})")
    elif step.kind == "type":
        seq = value_expr if value_expr is not None else repr(step.value or "")
        if use_locator:
            lines.append(f"    page.locator({selector!r}).first.click()")
            lines.append(f"    page.locator({selector!r}).first.fill('')")
            lines.append(f"    page.locator({selector!r}).first.press_sequentially({seq}, delay=100)")
        else:
            lines.append(f"    page.click({selector!r})")
            lines.append(f"    page.fill({selector!r}, '')")
            lines.append(f"    page.press_sequentially({selector!r}, {seq}, delay=100)")
    elif step.kind == "press":
        key = step.key or step.value or "Enter"
        lines.append(f"    page.locator({selector!r}).first.press({key!r})" if use_locator else f"    page.press({selector!r}, {key!r})")
    elif step.kind == "check":
        lines.append(f"    page.locator({selector!r}).first.check()" if use_locator else f"    page.check({selector!r})")
    elif step.kind == "uncheck":
        lines.append(f"    page.locator({selector!r}).first.uncheck()" if use_locator else f"    page.uncheck({selector!r})")
    else:
        raise ValueError(f"Unsupported step kind for emission: {step.kind}")

    if step.expected_effect == "url_changed":
        lines.append("    page.wait_for_load_state()")
    return lines


def _emit_step(
    step: TrajectoryStep, *, pre_action_ms: int = 0, settle_after_nav_ms: int = 0,
    value_expr: str | None = None,
) -> str:
    """Emits the Python call(s) for a single TrajectoryStep."""
    if step.kind == "goto":
        lines = []
        if pre_action_ms > 0:
            lines.append("    _pace(page)")
        target_url = _best_selector(step)
        lines.append(f"    page.goto({target_url!r})")
        if settle_after_nav_ms > 0:
            lines.append("    try:")
            lines.append(f'        page.wait_for_load_state("networkidle", timeout={settle_after_nav_ms})')
            lines.append("    except Exception:")
            lines.append("        pass")
        elif step.expected_effect == "url_changed":
            lines.append("    page.wait_for_load_state()")
        return "\n".join(lines)

    selector = _best_selector(step)
    if selector.startswith(_CODEGEN_EXPR_PREFIX):
        raw_expr = selector[len(_CODEGEN_EXPR_PREFIX):]
        return "\n".join(_emit_locator_call(
            raw_expr, step, pre_action_ms=pre_action_ms, settle_after_nav_ms=settle_after_nav_ms,
            value_expr=value_expr,
        ))

    return "\n".join(_emit_css_step(
        step, selector, pre_action_ms=pre_action_ms, settle_after_nav_ms=settle_after_nav_ms,
        value_expr=value_expr,
    ))


def _emit_step_with_fallback(
    step: TrajectoryStep, *, pre_action_ms: int = 0, settle_after_nav_ms: int = 0,
    value_expr: str | None = None,
) -> list[str]:
    """Emits code that tries candidates in ranked order."""
    candidates = [
        c["selector"] for c in sorted(step.selector_candidates, key=lambda c: c["rank"])
        if not (c["selector"].startswith("#") and _looks_session_random(c["selector"].lstrip("#")))
    ]
    if not candidates:
        candidates = [c["selector"] for c in sorted(step.selector_candidates, key=lambda c: c["rank"])]

    if len(candidates) <= 1 or step.kind == "goto":
        return [_emit_step(step, pre_action_ms=pre_action_ms, settle_after_nav_ms=settle_after_nav_ms, value_expr=value_expr)]

    has_codegen_expr = any(c.startswith(_CODEGEN_EXPR_PREFIX) for c in candidates)
    if has_codegen_expr:
        return [_emit_step(step, pre_action_ms=pre_action_ms, settle_after_nav_ms=settle_after_nav_ms, value_expr=value_expr)]

    visible_candidates = [_prefer_visible(c, step.force_click) for c in candidates]
    candidate_reprs = ", ".join(repr(c) for c in visible_candidates)

    lines: list[str] = []
    if pre_action_ms > 0:
        lines.append("        _pace(page)")

    lines.append(f"        _last_err = None")
    lines.append(f"        for _sel in [{candidate_reprs}]:")
    lines.append("            try:")

    if step.kind == "click":
        click_call = ".click(force=True)" if step.force_click else ".click()"
        lines.append(f"                page.locator(_sel).first{click_call}")
    elif step.kind == "dblclick":
        lines.append("                page.locator(_sel).first.dblclick()")
    elif step.kind == "hover":
        lines.append("                page.locator(_sel).first.hover()")
        lines.append("                page.wait_for_timeout(900)")
    elif step.kind == "hover_click":
        lines.append("                page.locator(_sel).first.hover()")
        lines.append("                page.wait_for_timeout(900)")
        lines.append("                page.locator(_sel).first.click()")
    elif step.kind == "dispatch_click":
        lines.append('                page.locator(_sel).first.dispatch_event("click")')
    elif step.kind == "fill":
        val = value_expr if value_expr is not None else repr(step.value or "")
        lines.append(f"                page.locator(_sel).first.fill({val})")
    elif step.kind == "type":
        seq = value_expr if value_expr is not None else repr(step.value or "")
        lines.append("                page.locator(_sel).first.click()")
        lines.append("                page.locator(_sel).first.fill('')")
        lines.append(f"                page.locator(_sel).first.press_sequentially({seq}, delay=100)")
    elif step.kind == "press":
        key = step.key or step.value or "Enter"
        lines.append(f"                page.locator(_sel).first.press({key!r})")
    elif step.kind == "check":
        lines.append("                page.locator(_sel).first.check()")
    elif step.kind == "uncheck":
        lines.append("                page.locator(_sel).first.uncheck()")
    else:
        return [_emit_step(step, pre_action_ms=pre_action_ms, settle_after_nav_ms=settle_after_nav_ms, value_expr=value_expr)]

    lines.append("                _last_err = None")
    lines.append("                break")
    lines.append("            except Exception as _e:")
    lines.append("                _last_err = _e")
    lines.append("        if _last_err is not None:")
    lines.append("            raise _last_err")

    if step.expected_effect == "url_changed":
        lines.append("        page.wait_for_load_state()")

    return lines


def _wrap_step_with_diagnostics(step_lines: list[str], step_index: int, label: str) -> list[str]:
    """Wraps step lines in try/except that calls dump_failure_context."""
    safe_label = label.replace('"', "'")
    lines = ["try:"]
    lines.extend(f"    {l}" for l in step_lines)
    lines.append("except Exception as e:")
    lines.append(f'    dump_failure_context(page, page, {step_index}, "{safe_label}", str(e), diagnostics_dir)')
    lines.append("    raise")
    return lines


def _build_argparse_and_main_params(classified: "ClassifiedParams") -> str:
    """Generates the argparse setup + params-dict construction."""
    lines = ["    parser = argparse.ArgumentParser()"]
    for p in classified.params:
        lines.append(f'    parser.add_argument("--{p.cli_name}", default={p.default_value!r})')
    lines.append('    parser.add_argument("--headless", action="store_true", default=True)')
    lines.append('    parser.add_argument("--headed", dest="headless", action="store_false")')
    lines.append('    parser.add_argument("--diagnostics-dir", default="diagnostics")')
    lines.append('    parser.add_argument("--batch", default="")')
    lines.append("    args = parser.parse_args()")
    lines.append("")
    lines.append("    params = {")
    for s in classified.secrets:
        lines.append(f'        "{s.label}": os.environ.get("{s.env_var}", ""),')
    lines.append('        "headless": args.headless,')
    lines.append('        "diagnostics_dir": args.diagnostics_dir,')
    lines.append("    }")
    for p in classified.params:
        attr = p.cli_name.replace("-", "_")
        lines.append(f'    params["{attr}"] = args.{attr}')
    return "\n".join(lines)


def _reindent_for_login_body(emitted: str) -> str:
    """Strips leading whitespace from every line and re-prefixes with 4 spaces."""
    return "\n".join(f"    {line.lstrip()}" for line in emitted.split("\n") if line.strip())


def _step_page_expr(step: TrajectoryStep) -> str:
    """The page expression a step acts on — `page` or `_page(page, N)` for
    popup steps — used for the per-step _settle_frames() call (matches the
    old app's `_settle_frames(${p.pageVar})` before every planned step)."""
    selector = _best_selector(step)
    if selector.startswith(_CODEGEN_EXPR_PREFIX):
        expr = selector[len(_CODEGEN_EXPR_PREFIX):]
        m = _PAGE_ROOT_RE.match(expr)
        if m and m.group(1) != "page":
            return f"_page(page, {int(m.group(1)[4:])})"
    return "page"


def _build_login_block(
    trajectory: ReferenceTrajectory, classified: "ClassifiedParams", *, success_signal: dict | None = None,
    start_url: str = "",
) -> tuple[str, set[int]]:
    """Emits login()/attempt_login_once()/dismiss_post_login_continue()."""
    password_entries = [s for s in classified.secrets if s.label == "password"]
    if not password_entries:
        return "", set()
    password_secret = password_entries[0]
    username_entries = [s for s in classified.secrets if s.label == "username"]

    steps_by_index = {s.index: s for s in trajectory.steps}
    password_step = steps_by_index[password_secret.step_index]
    consumed = {password_step.index}

    def _fill_from_params(step: TrajectoryStep, params_key: str) -> str:
        selector = _best_selector(step)
        if selector.startswith(_CODEGEN_EXPR_PREFIX):
            expr = selector[len(_CODEGEN_EXPR_PREFIX):]
            m = _PAGE_ROOT_RE.match(expr)
            if not m:
                root, rest = "page", expr
            else:
                root, rest = m.group(1), m.group(2)
            receiver = f"_page(page, {int(root[4:])})" if root != "page" else "page"
            return f'    {receiver}.{rest}.fill(params["{params_key}"])'
        selector = _prefer_visible(selector, step.force_click)
        if ">>" in selector:
            return f'    page.locator({selector!r}).first.fill(params["{params_key}"])'
        return f'    page.fill({selector!r}, params["{params_key}"])'

    body: list[str] = []
    body.append("def attempt_login_once(page, params):")
    if username_entries:
        username_step = steps_by_index[username_entries[0].step_index]
        # The recording usually opens with a focus CLICK on the username field
        # right before the fill (eCW records click-into-field then types). Those
        # clicks must be swept into the login block too — left in the main flow
        # they replay POST-login, when the field no longer exists, and fail.
        focus_lines: list[str] = []
        focus_index = username_step.index - 1
        while focus_index in steps_by_index:
            focus_step = steps_by_index[focus_index]
            if (
                focus_step.kind in ("click", "dblclick")
                and _best_selector(focus_step) == _best_selector(username_step)
            ):
                focus_lines.append(_reindent_for_login_body(_emit_step(focus_step)))
                consumed.add(focus_index)
                focus_index -= 1
            else:
                break
        body.extend(reversed(focus_lines))
        consumed.add(username_step.index)
        body.append(_fill_from_params(username_step, "username"))
    between_indices = sorted(
        i for i in steps_by_index
        if (username_entries and username_entries[0].step_index < i < password_step.index)
    )
    for i in between_indices:
        body.append(_reindent_for_login_body(_emit_step(steps_by_index[i])))
        consumed.add(i)
    body.append(_fill_from_params(password_step, "password"))
    submit_index = password_step.index + 1
    if submit_index in steps_by_index:
        body.append(_reindent_for_login_body(_emit_step(steps_by_index[submit_index])))
        consumed.add(submit_index)
    body.append("    page.wait_for_timeout(3000)")
    body.append("")
    body.append("")

    body.append("def dismiss_post_login_continue(page):")
    body.append("    for _ in range(10):")
    body.append('        candidates = ["role=button[name=\\"\\u00d7\\"]", "role=button[name=\\"Cancel\\"]"]')
    body.append("        clicked = False")
    body.append("        for sel in candidates:")
    body.append("            loc = page.locator(sel).first")
    body.append("            try:")
    body.append("                if loc.count() > 0 and loc.is_visible():")
    body.append("                    loc.click(timeout=5000)")
    body.append("                    page.wait_for_timeout(1000)")
    body.append("                    clicked = True")
    body.append("                    break")
    body.append("            except Exception:")
    body.append("                pass")
    body.append("        if not clicked:")
    body.append("            break")
    body.append("")
    body.append("")

    signal_check = "True"
    signal_css = None
    if success_signal:
        if success_signal.get("css_selector"):
            signal_css = success_signal["css_selector"]
        elif success_signal.get("url_contains"):
            signal_check = f'"{success_signal["url_contains"]}" in page.url'

    body.append("def login(page, params):")
    body.append('    last_error = None')
    body.append("    for attempt in range(1, 4):")
    body.append("        if attempt > 1:")
    body.append('            log_step("Retrying login (attempt " + str(attempt) + "/3) — previous: " + str(last_error))')
    body.append("        try:")
    body.append("            attempt_login_once(page, params)")
    body.append("            dismiss_post_login_continue(page)")
    if signal_css:
        body.append(f'            log_step("Verifying login via successSignal: " + {signal_css!r})')
        body.append(f"            page.locator({signal_css!r}).first.wait_for(timeout=60000)")
        body.append('            log_step("Login successful" if attempt == 1 else "Login successful (after " + str(attempt) + " attempts)")')
    else:
        body.append("            deadline = time.time() + 60")
        body.append(f"            while time.time() < deadline and not ({signal_check}):")
        body.append("                page.wait_for_timeout(500)")
        body.append(f"            if not ({signal_check}):")
        body.append('                raise Exception("login success signal never appeared")')
    body.append("            return")
    body.append("        except Exception as e:")
    body.append("            last_error = e")
    body.append('    raise Exception("Login failed after 3 attempt(s). Last error: " + str(last_error))')
    body.append("")

    return "\n".join(body) + "\n", consumed


def _emit_extract_step() -> list[str]:
    """Runs the DOM-walking heuristic serializer across every frame."""
    return [
        "_extract_n += 1",
        "_merged = {'fields': {}, 'tables': []}",
        "for _fr in page.frames:",
        "    try:",
        f"        _res = _fr.evaluate({_SERIALIZER_JS!r})",
        "    except Exception:",
        "        continue",
        "    _data = (_res or {}).get('data') or {}",
        "    _merged['fields'].update(_data.get('fields') or {})",
        "    _merged['tables'].extend(_data.get('tables') or [])",
        'result["extractions"][f"click_{_extract_n}"] = {"url": page.url, "data": _merged}',
    ]


def _emit_download_step(action_lines: list[str]) -> list[str]:
    """Wraps a download-triggering step's action lines in a CONTEXT-scoped download wait."""
    lines = ['with page.context.expect_event("download", timeout=300000) as _dl_info:']
    lines.extend(f"    {line}" for line in action_lines)
    lines.append("_download = _dl_info.value")
    lines.append('_dl_dir = os.path.join(diagnostics_dir, "..", "results")')
    lines.append("os.makedirs(_dl_dir, exist_ok=True)")
    lines.append("_dl_path = os.path.join(_dl_dir, _download.suggested_filename)")
    lines.append("_download.save_as(_dl_path)")
    lines.append('result["downloaded_files"].append({"filename": _download.suggested_filename, "path": _dl_path})')
    return lines


def emit_golden_script(
    trajectory: ReferenceTrajectory, *, start_url: str,
    portal_domain: str = "unknown", portal_name: str = "Unknown Portal", confidence: float | None = None,
    success_signal: dict | None = None,
    pre_action_ms: int = 0, settle_after_nav_ms: int = 0,
) -> str:
    """Deterministic, no LLM — converts a verified trajectory into a self-contained Playwright script."""
    classified = classify_step_values(trajectory, portal_domain)
    login_block, consumed_indices = _build_login_block(
        trajectory, classified, success_signal=success_signal, start_url=start_url,
    )

    remaining_steps = [s for s in trajectory.steps if s.index not in consumed_indices]

    value_expr_by_index: dict[int, str] = {}
    for s in classified.secrets:
        if s.step_index not in consumed_indices:
            value_expr_by_index[s.step_index] = f'params["{s.label}"]'
    for p in classified.params:
        if p.step_index not in consumed_indices:
            attr = p.cli_name.replace("-", "_")
            value_expr_by_index[p.step_index] = f'params["{attr}"]'

    body_lines: list[str] = []
    for step in remaining_steps:
        if step.kind == "extract":
            body_lines.extend(f"    {line}" for line in _emit_extract_step())
            continue
        page_expr = _step_page_expr(step)
        body_lines.append(f"    _settle_frames({page_expr})")
        raw_lines = _emit_step_with_fallback(
            step, pre_action_ms=pre_action_ms, settle_after_nav_ms=settle_after_nav_ms,
            value_expr=value_expr_by_index.get(step.index),
        )
        dedented = textwrap.dedent("\n".join(raw_lines))
        stripped = [l for l in dedented.split("\n") if l.strip()]
        label = f"{step.kind} step {step.index}"
        if step.expects_download:
            wrapped = _emit_download_step(stripped)
        else:
            wrapped = stripped
        diagnosed = _wrap_step_with_diagnostics(wrapped, step.index, label)
        body_lines.extend(f"    {line}" for line in diagnosed)

    header = _HEADER_TOP
    if pre_action_ms > 0:
        header += _PACE_HELPER_TEMPLATE.format(pre_action_ms=pre_action_ms)
    header += "\n" + _SETTLE_FRAMES_HELPER
    header += "\n" + _FIND_FRAME_WITH_ELEMENT_HELPER
    header += "\n" + _DUMP_FAILURE_CONTEXT_HELPER
    header += "\n" + _PRINT_CAPTURE_HELPER
    if login_block:
        header += "\n" + login_block

    review_header = _build_review_header(
        trajectory, portal_name=portal_name, confidence=confidence, classified=classified,
    )

    run_body = "\n".join(body_lines)

    main_params_block = _build_argparse_and_main_params(classified)

    script = (
        review_header
        + header
        + "\n"
        + "def run_flow(playwright, page, params, diagnostics_dir):\n"
        + "    result = {\"fileCount\": 0, \"filesTransferred\": [], \"extractions\": {}, \"downloaded_files\": []}\n"
        + "    _extract_n = 0\n"
        + f"    page.goto({start_url!r})\n"
        + (f"    login(page, params)\n" if login_block else "")
        + run_body + "\n"
        + "    if not result[\"extractions\"]:\n"
        + "        del result[\"extractions\"]\n"
        + "    result[\"fileCount\"] = len(result[\"downloaded_files\"])\n"
        + "    result[\"filesTransferred\"] = [f[\"filename\"] for f in result[\"downloaded_files\"]]\n"
        + "    return result\n"
        + "\n\n"
        + "def run(playwright, params):\n"
        + "    browser = playwright.chromium.launch(headless=params.get(\"headless\", True))\n"
        + "    context = browser.new_context(\n"
        + "        viewport={\"width\": 1920, \"height\": 1080},\n"
        + "        locale=\"en-US\",\n"
        + "        timezone_id=\"America/Chicago\",\n"
        + "        accept_downloads=True,\n"
        + "    )\n"
        + f"    context.add_init_script({_PRINT_CAPTURE_INIT_SCRIPT!r})\n"
        + "    context.expose_binding(\"__atlasOnPrint\", _atlas_on_print)\n"
        + "    page = context.new_page()\n"
        + "    diagnostics_dir = params.get(\"diagnostics_dir\", \"diagnostics\")\n"
        + "    try:\n"
        + "        _batch = params.get(\"_batch\")\n"
        + "        if _batch:\n"
        + "            _post_login_url = None\n"
        + "            _results = []\n"
        + "            for _i, _row in enumerate(_batch):\n"
        + "                if _i > 0 and _post_login_url:\n"
        + "                    page.goto(_post_login_url)\n"
        + "                    page.wait_for_timeout(2000)\n"
        + "                _rp = dict(params)\n"
        + "                _rp.update(_row)\n"
        + "                try:\n"
        + "                    _r = run_flow(playwright, page, _rp, diagnostics_dir)\n"
        + "                except Exception as _be:\n"
        + "                    _r = {\"fileCount\": 0, \"filesTransferred\": [], \"downloaded_files\": [], \"error\": str(_be)[:400]}\n"
        + "                _row_pdfs = _flush_print_pdfs(playwright, os.path.join(diagnostics_dir, \"..\", \"results\"))\n"
        + "                for _pdf in _row_pdfs:\n"
        + "                    _r[\"downloaded_files\"].append(_pdf)\n"
        + "                _r[\"fileCount\"] = len(_r[\"downloaded_files\"])\n"
        + "                _r[\"filesTransferred\"] = [f[\"filename\"] for f in _r[\"downloaded_files\"]]\n"
        + "                _r[\"inputs\"] = _row\n"
        + "                _results.append(_r)\n"
        + "                _post_login_url = page.url\n"
        + "            return {\"batch\": _results}\n"
        + "        else:\n"
        + "            _r = run_flow(playwright, page, params, diagnostics_dir)\n"
        + "            _pdfs = _flush_print_pdfs(playwright, os.path.join(diagnostics_dir, \"..\", \"results\"))\n"
        + "            for _pdf in _pdfs:\n"
        + "                _r[\"downloaded_files\"].append(_pdf)\n"
        + "            _r[\"fileCount\"] = len(_r[\"downloaded_files\"])\n"
        + "            _r[\"filesTransferred\"] = [f[\"filename\"] for f in _r[\"downloaded_files\"]]\n"
        + "            return _r\n"
        + "    finally:\n"
        + "        context.close()\n"
        + "        browser.close()\n"
        + "\n\n"
        + "def main():\n"
        + main_params_block + "\n"
        + "    if args.batch:\n"
        + "        with open(args.batch) as _bf:\n"
        + "            params[\"_batch\"] = json.load(_bf)\n"
        + "    with sync_playwright() as playwright:\n"
        + "        result = run(playwright, params)\n"
        + "        print(json.dumps(result))\n"
        + "\n\n"
        + 'if __name__ == "__main__":\n'
        + "    main()\n"
    )
    return script