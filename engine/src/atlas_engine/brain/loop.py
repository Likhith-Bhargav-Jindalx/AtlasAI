import ast
import dataclasses
import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from atlas_engine.brain.cache import ActionCacheStore, compute_cache_key
from atlas_engine.brain.evaluate import StepOutcome, evaluate
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.brain.repair import RepairAction, think
from atlas_engine.llm.client import LlmClient
from atlas_engine.llm.sanitize import sanitize_dom

_REDACTED_RE = re.compile(r"<(?:REDACTED|SECRET):([A-Za-z0-9_]+)>")


def _rematerialize(val: str | None) -> str:
    """A scrubbed value <REDACTED:ENV> or <SECRET:ENV> (secret or PHI input)
    is re-materialized from os.environ at replay/brain execution time so the
    live browser types the real credential/input value, never the placeholder."""
    if not val:
        return ""
    m = _REDACTED_RE.fullmatch(val.strip())
    if m:
        env_var = m.group(1)
        return os.environ.get(env_var, "")
    return _REDACTED_RE.sub(lambda match: os.environ.get(match.group(1), ""), val)


def _settle_frames(pg, timeout_ms: int = 3000) -> None:
    """Bounded wait for the page's iframe tree to stop changing — i.e. nested frames
    finished (re)loading. Portals like eCW / Cognos / Epic reload nested frames on
    navigation, and resolving before they load fails."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    prev = None
    stable = 0
    while time.monotonic() < deadline:
        try:
            sig = tuple(sorted((f.url or "") for f in pg.frames))
        except Exception:
            return
        if sig == prev:
            stable += 1
            if stable >= 2:
                return
        else:
            stable = 0
        prev = sig
        try:
            pg.wait_for_timeout(300)
        except Exception:
            return


# Default ceiling per action attempt (15s default for un-paced steps; codegen sets 60s).
_INITIAL_ATTEMPT_TIMEOUT_MS = 15000

# How many bounded LLM corrections the brain applies to one step before
# giving up on it (mirrors the old app's BRAIN_MAX_THINKS_PER_STEP default).
_MAX_THINKS_PER_STEP = 6

# Selectors parsed from the native codegen recorder's get_by_* lines are NOT
# CSS — they carry this prefix plus the locator call expression (everything
# after `page.`), e.g. "codegen_expr:get_by_role(\"textbox\", name=\"U\")".
_CODEGEN_EXPR_PREFIX = "codegen_expr:"

_PAGE_ROOT_RE = re.compile(r"^(page\d*)\.")

# What the element that actually resolved LOOKS like — the recorded identity
# the reference replay captured, so emission can prefer a stable id and the
# prompt can point at the same element even when the selector changed.
_IDENTITY_JS = (
    "el => ({tag: el.tagName.toLowerCase(), id: el.id || null, "
    "cls: (typeof el.className === 'string' ? el.className : null), "
    "text: (el.innerText || '').slice(0, 80), "
    "testid: el.getAttribute('data-testid'), "
    "idUnique: (el.id ? Array.prototype.filter.call(el.ownerDocument.querySelectorAll('*'), function(n){return n.id === el.id}).length === 1 : false)})"
)


class BrainLoopResult(list):
    """list[StepOutcome] (one per executed/verified step, in order — including
    the step that ultimately failed) plus the verified trajectory and failure
    metadata. Extends list so existing callers that do `outcomes[0]`,
    `[o.on_track for o in outcomes]` or `all(o.on_track ...)` keep working;
    new callers use `.success` / `.trajectory` / `.failed_step`."""

    def __init__(
        self,
        outcomes: list[StepOutcome],
        *,
        trajectory: ReferenceTrajectory,
        thinks: list[dict],
        failed_step: TrajectoryStep | None = None,
        failure_reason: str | None = None,
        downloads: list[str] | None = None,
    ):
        super().__init__(outcomes)
        self.trajectory = trajectory
        self.thinks = thinks
        self.failed_step = failed_step
        self.failure_reason = failure_reason
        self.downloads = downloads or []

    @property
    def success(self) -> bool:
        return self.failed_step is None and self.failure_reason is None


def _page_for_index(page, index: int, timeout: float = 15.0):
    """Resolves the Nth page of the shared browser context — page 0 is the
    page the run started on; pages 1+ are popups the app opened (e.g. a
    target=_blank report viewer). Waits briefly for the popup to appear."""
    if index == 0:
        return page
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pages = page.context.pages
        if len(pages) > index:
            return pages[index]
        page.wait_for_timeout(200)
    raise TimeoutError(f"Popup page {index} never opened")


def _target_page(page, selector: str):
    """The page a step's selector refers to — the main page, or a popup page
    when the selector carries a `page1.` codegen root."""
    if selector.startswith(_CODEGEN_EXPR_PREFIX):
        m = _PAGE_ROOT_RE.match(selector[len(_CODEGEN_EXPR_PREFIX):])
        if m and m.group(1) != "page":
            return _page_for_index(page, int(m.group(1)[4:]))
    return page


def _build_locator(page, expr: str):
    """Evaluates a safe codegen locator expression like
    `get_by_role("textbox", name="Username").nth(1)` (or a full chain such as
    `page1.locator("#x").content_frame.get_by_role("button", name="OK")`)
    against the live page by walking its AST — only page/`re` names, attribute
    access and calls with literal arguments are allowed, so this is equivalent
    to typing the codegen line by hand."""
    if not _PAGE_ROOT_RE.match(expr):
        expr = f"page.{expr}"
    tree = ast.parse(expr, mode="eval")

    def eval_node(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id == "page":
                return page
            if node.id == "re":
                return re
            m = re.fullmatch(r"page(\d+)", node.id)
            if m:
                return _page_for_index(page, int(m.group(1)))
            raise ValueError(f"Unsupported locator name: {node.id}")
        if isinstance(node, ast.Attribute):
            return getattr(eval_node(node.value), node.attr)
        if isinstance(node, ast.Call):
            func = eval_node(node.func)
            args = [eval_node(a) for a in node.args]
            kwargs = {kw.arg: eval_node(kw.value) for kw in node.keywords if kw.arg is not None}
            return func(*args, **kwargs)
        raise ValueError(f"Unsupported locator expression node: {ast.dump(node)}")

    return eval_node(tree.body)


def _locator_for_resolved(page, selector: str):
    if selector.startswith(_CODEGEN_EXPR_PREFIX):
        return _build_locator(page, selector[len(_CODEGEN_EXPR_PREFIX):])
    return page.locator(selector)


def _locator_for_proposal(page, selector: dict | str):
    """Builds a Playwright locator from a brain-proposed SimpleSelector dict or
    a plain CSS/codegen-expr string. Returns None when the shape isn't
    understood."""
    if selector is None:
        return None
    if isinstance(selector, str):
        return _locator_for_resolved(page, selector)
    kind = selector.get("kind")
    value = selector.get("value")
    if kind == "css":
        return page.locator(value)
    if kind == "role":
        return page.get_by_role(selector.get("role") or "button", name=value)
    if kind == "text":
        return page.get_by_text(value, exact=True)
    if kind == "testid":
        return page.get_by_test_id(value)
    if kind == "label":
        return page.get_by_label(value, exact=True)
    if kind == "title":
        return page.get_by_title(value)
    if kind == "placeholder":
        return page.get_by_placeholder(value)
    if kind == "alt":
        return page.get_by_alt_text(value)
    return None


def resolve_count(page, selector: dict | str) -> tuple[int, int]:
    """(count, visible) of a proposed selector on the live page — the brain's
    proof that a proposal points at something real before it is applied,
    mirroring the old runner's `resolve` command."""
    loc = _locator_for_proposal(page, selector)
    if loc is None:
        return 0, 0
    try:
        count = loc.count()
    except Exception:
        return 0, 0
    if count == 0:
        return 0, 0
    visible = 0
    for i in range(min(count, 12)):
        try:
            if loc.nth(i).is_visible():
                visible += 1
        except Exception:
            pass
    return count, visible


def _simple_selector_to_expr(selector: dict | str) -> str:
    """Turns a brain-proposed SimpleSelector into something run_step can act
    on: a plain CSS string, or a codegen locator expression for the get_by_*
    kinds (re-evaluated via the AST locator builder)."""
    if isinstance(selector, str):
        return selector
    kind = selector.get("kind")
    value = selector.get("value")
    if kind == "css":
        return value
    if kind == "role":
        role = selector.get("role") or "button"
        return f'page.get_by_role({role!r}, name={value!r})'
    if kind == "text":
        return f"page.get_by_text({value!r}, exact=True)"
    if kind == "testid":
        return f"page.get_by_test_id({value!r})"
    if kind == "label":
        return f"page.get_by_label({value!r}, exact=True)"
    if kind == "title":
        return f"page.get_by_title({value!r})"
    if kind == "placeholder":
        return f"page.get_by_placeholder({value!r})"
    if kind == "alt":
        return f"page.get_by_alt_text({value!r})"
    return value


def _first_selector(step: TrajectoryStep) -> str:
    ranked = sorted(step.selector_candidates, key=lambda c: c["rank"])
    return ranked[0]["selector"]


def _save_shot(page, shot_dir: str | None, tag: str) -> str | None:
    if not shot_dir:
        return None
    try:
        Path(shot_dir).mkdir(parents=True, exist_ok=True)
        path = str(Path(shot_dir) / f"{tag}.png")
        page.screenshot(path=path)
        return path
    except Exception:
        return None


def _resolve_locator(page, candidates: list[dict], timeout_ms: int | None):
    """Best-first candidate resolution with visible preference on multi-match,
    re-polling until the ceiling — a faithful port of the old runner's
    `resolve`: try candidates in order; the first with a single match wins; a
    multi-match prefers the VISIBLE one (the hidden-duplicate trap); nothing
    matched → sleep 0.4s and retry until the deadline. Raises TimeoutError
    when no candidate matches in time. Returns (locator, used_candidate_index,
    match_count)."""
    deadline = time.monotonic() + (timeout_ms or 0) / 1000.0
    last = None
    while True:
        for ci, cand in enumerate(candidates):
            try:
                loc = _locator_for_resolved(page, cand["selector"])
                n = loc.count()
            except Exception as e:
                last = str(e)[:200]
                continue
            if n == 0:
                continue
            if n == 1:
                return loc.first, ci, n
            for i in range(min(n, 12)):
                try:
                    cand_loc = loc.nth(i)
                    if cand_loc.is_visible():
                        return cand_loc, ci, n
                except Exception:
                    pass
            return loc.first, ci, n
        if time.monotonic() >= deadline:
            break
        try:
            page.wait_for_timeout(400)
        except Exception:
            time.sleep(0.4)
    raise TimeoutError(f"resolve: no locator candidate matched within {timeout_ms}ms (last error: {last})")


def _act_on_locator(locator, step: TrajectoryStep, *, force: bool = False, timeout_ms: float | None = None, key: str | None = None) -> None:
    val = _rematerialize(step.value)
    if step.kind in ("click", "dblclick"):
        getattr(locator, step.kind)(force=force, timeout=timeout_ms)
    elif step.kind == "hover":
        locator.hover(force=force, timeout=timeout_ms)
        locator.page.wait_for_timeout(900)
    elif step.kind == "hover_click":
        locator.hover(timeout=timeout_ms)
        locator.page.wait_for_timeout(900)
        locator.click(force=force, timeout=timeout_ms)
    elif step.kind == "dispatch_click":
        locator.dispatch_event("click")
    elif step.kind == "type":
        locator.click(force=force, timeout=timeout_ms)
        locator.fill("")
        locator.press_sequentially(val, delay=100)
    elif step.kind == "check":
        locator.check(force=force, timeout=timeout_ms)
    elif step.kind == "uncheck":
        locator.uncheck(force=force, timeout=timeout_ms)
    elif step.kind == "fill":
        locator.fill(val, timeout=timeout_ms)
    elif step.kind == "press":
        locator.press(key or val or "Enter", timeout=timeout_ms)
    elif step.kind == "select_option":
        locator.select_option(val, timeout=timeout_ms)
    else:
        raise ValueError(f"Unsupported step kind: {step.kind}")


def _act(
    page,
    selector: str,
    step: TrajectoryStep,
    *,
    force: bool = False,
    timeout_ms: float | None = None,
    x: float | None = None,
    y: float | None = None,
    key: str | None = None,
    loc=None,
) -> None:
    if step.kind == "goto":
        nav_timeout = max(timeout_ms or 0, 60000)
        page.goto(selector, wait_until="domcontentloaded", timeout=nav_timeout)
        return
    if step.kind == "close":
        page.close()
        return
    if step.kind == "click_xy":
        page.mouse.click(x or 0, y or 0)
        return
    if loc is None and selector.startswith(_CODEGEN_EXPR_PREFIX):
        loc = _build_locator(page, selector[len(_CODEGEN_EXPR_PREFIX):])
    if loc is None:
        loc = page.locator(selector)
    _act_on_locator(loc, step, force=force, timeout_ms=timeout_ms, key=key)


def run_step(
    page,
    step: TrajectoryStep,
    *,
    selector: str | None = None,
    force: bool = False,
    timeout_ms: float | None = _INITIAL_ATTEMPT_TIMEOUT_MS,
    shot_dir: str | None = None,
    x: float | None = None,
    y: float | None = None,
    key: str | None = None,
) -> StepOutcome:
    """Executes ONE trajectory step against the live page and gathers the
    evidence the brain evaluates and (when off-track) thinks over: the error,
    input ground truth, popup/download events, the resolved element's identity
    and match count, and pre/post screenshots (when shot_dir is given). A
    faithful port of the old runner's exec_entry — candidates are resolved
    best-first with visible preference and the chosen locator is acted on."""
    resolved_selector = selector or _first_selector(step)
    if step.kind == "close":
        page.close()
        return evaluate(
            step,
            url_before=page.url, url_after=page.url,
            dom_before="", dom_after="",
            resolved_selector=resolved_selector,
            elapsed_ms=0,
        )
    # A codegen popup step acts on a different page (page1, ...) — measure
    # and act on THAT page, not the main one.
    target = _target_page(page, resolved_selector)
    pages_before = len(page.context.pages)
    t0 = time.monotonic()

    url_before = ""
    dom_before = ""
    shot_before = None
    try:
        url_before = target.url
    except Exception:
        pass
    try:
        dom_before = target.content()
    except Exception:
        pass  # page mid-navigation — the old runner's state() never lets this abort a step
    if shot_dir:
        shot_before = _save_shot(target, shot_dir, f"step{step.index}")

    timeout = step.wait_ceiling_ms if step.wait_ceiling_ms is not None else timeout_ms

    error: str | None = None
    resolved_identity: dict | None = None
    input_value: str | None = None
    used_locator_index: int | None = None
    match_count: int | None = None
    popup_opened = False
    download_started = False
    download_saved: str | None = None
    enters_iframe = "content_frame" in resolved_selector or "frame_locator" in resolved_selector

    loc = None
    if step.kind not in ("goto", "close", "click_xy"):
        if hasattr(target, "frames") and len(target.frames) > 1:
            _settle_frames(target, 3000)
        # An explicit `selector` overrides resolution entirely — it is the ONE
        # candidate to try (used by the repair cache's re-verify).
        candidates = [{"selector": selector, "rank": 0}] if selector else step.selector_candidates
        try:
            loc, used_locator_index, match_count = _resolve_locator(target, candidates, timeout)
        except Exception as exc:
            error = str(exc)[:700]
        else:
            # Resolve can match a still-hidden element (the view may be
            # mid-render) — wait for visibility first (bounded), then read what
            # the resolved element looks like PRE-gesture so a navigation step
            # keeps the element it acted on.
            try:
                loc.wait_for(state="visible", timeout=min(timeout or 15000, 15000))
            except Exception:
                pass
            try:
                resolved_identity = loc.evaluate(_IDENTITY_JS)
            except Exception:
                pass

    if error is None:
        try:
            if step.expects_download:
                with page.expect_download(timeout=max(timeout or 0, 120000)) as dli:
                    _act(target, resolved_selector, step, force=force, timeout_ms=timeout, x=x, y=y, key=key, loc=loc)
                d = dli.value
                download_started = True
                if shot_dir:
                    dl_dir = Path(shot_dir) / "downloads"
                    dl_dir.mkdir(parents=True, exist_ok=True)
                    save_path = str(dl_dir / d.suggested_filename)
                    d.save_as(save_path)
                    download_saved = save_path
            elif step.expects_popup:
                with page.context.expect_page(timeout=max(timeout or 0, 30000)) as pi:
                    _act(target, resolved_selector, step, force=force, timeout_ms=timeout, x=x, y=y, key=key, loc=loc)
                try:
                    pi.value.wait_for_load_state("domcontentloaded", timeout=30000)
                except Exception:
                    pass
                popup_opened = True
            else:
                _act(target, resolved_selector, step, force=force, timeout_ms=timeout, x=x, y=y, key=key, loc=loc)
            popup_opened = popup_opened or len(page.context.pages) > pages_before
        except Exception as exc:
            error = str(exc)[:700]

    obs_pg = page.context.pages[-1] if len(page.context.pages) > pages_before else target
    try:
        obs_pg.wait_for_load_state("domcontentloaded")
        obs_pg.wait_for_timeout(700)
    except Exception:
        pass

    url_after = ""
    dom_after = ""
    shot_after = None
    try:
        url_after = obs_pg.url
    except Exception:
        pass
    try:
        dom_after = obs_pg.content()
    except Exception:
        pass  # page mid-navigation — same tolerance as the before-snapshot
    if shot_dir:
        shot_after = _save_shot(obs_pg, shot_dir, f"step{step.index}")

    if error is None and step.kind in ("fill", "type", "select_option"):
        try:
            input_value = loc.input_value(timeout=2000)
        except Exception:
            pass

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    return evaluate(
        step,
        url_before=url_before,
        url_after=url_after,
        dom_before=dom_before,
        dom_after=dom_after,
        resolved_selector=resolved_selector,
        error=error,
        input_value=input_value,
        popup_opened=popup_opened,
        download_started=download_started,
        download_saved=download_saved,
        enters_iframe=enters_iframe,
        resolved_identity=resolved_identity,
        match_count=match_count,
        used_locator_index=used_locator_index,
        shot_before=shot_before,
        shot_after=shot_after,
        elapsed_ms=elapsed_ms,
    )


def _off_track_reason(step: TrajectoryStep, outcome: StepOutcome) -> str:
    if outcome.error:
        return f"error: {outcome.error[:200]}"
    if step.expects_popup and not outcome.popup_opened:
        return "expected a popup/new page — none opened"
    if step.expects_download and not outcome.download_started:
        return "expected a download — none started"
    if step.kind in ("fill", "type") and step.value is not None and outcome.input_value == step.value:
        return "input ground-truth mismatch"
    if outcome.enters_iframe:
        return "iframe blind spot"
    return "reference shows this step changes the page; live page did not change (silent no-op)"


class BrainLoop:
    """perceive -> act -> evaluate -> think only if off-track, grounded in
    the reference evidence (recorded locators/identity/effects + reference
    screenshot) AND the live page (sanitized DOM + live screenshot) — a
    faithful port of the old app's brain loop:

      - the step order is MUTABLE: the brain can insert a missing opener step
        (executed next, the current step retried after), remove a recorded
        fumble (never emitted), or re-execute the previous step;
      - each repair is validated (resolve-count) against the live DOM before
        being applied, and a proposal that matches nothing is re-fed to the
        next think round as REJECTED feedback;
      - an EXACT repeat of a prior proposal is rejected the same way;
      - the think budget is checked BEFORE thinking (budget exhausted → the
        run fails with the step that broke it);
      - a human-only gate (captcha/mfa/antibot) fails the run with the
        blocker's evidence;
      - without `llm`, an off-track step fails the run immediately (tests that
        only exercise the happy path).
    """

    def __init__(
        self,
        page,
        on_off_track: Callable[[TrajectoryStep, StepOutcome], None] | None = None,
        llm: LlmClient | None = None,
        action_cache: ActionCacheStore | None = None,
        golden_script_id: uuid.UUID | None = None,
        max_thinks: int = _MAX_THINKS_PER_STEP,
    ):
        self.page = page
        self.on_off_track = on_off_track
        self.llm = llm
        self.action_cache = action_cache
        self.golden_script_id = golden_script_id
        self.max_thinks = max_thinks

    def run(
        self, trajectory: ReferenceTrajectory, *, shot_dir: str | None = None, goal: str | None = None,
    ) -> BrainLoopResult:
        """Runs the brain loop over the trajectory, returning a BrainLoopResult
        whose `.trajectory` is the VERIFIED trajectory (repairs folded in,
        removed/close steps excluded) and whose list body is one StepOutcome
        per executed step — including a failed final step, so
        `all(o.on_track for o in outcomes)` stays False for legacy callers."""
        steps = [dataclasses.replace(s) for s in trajectory.steps]
        verified: list[TrajectoryStep] = []
        outcomes: list[StepOutcome] = []
        thinks: list[dict] = []
        downloads: list[str] = []
        failed_step: TrajectoryStep | None = None
        failure_reason: str | None = None
        insert_seq = 0

        pos = 0
        while pos < len(steps):
            working = steps[pos]
            if working.kind == "close":
                pos += 1
                continue  # page closes are teardown, not flow

            attempts: list[str] = []
            done = False
            outcome: StepOutcome | None = None
            cache_key: str | None = None

            while not done:
                outcome = self._execute(working, shot_dir)
                if outcome.download_saved:
                    downloads.append(outcome.download_saved)

                if outcome.on_track:
                    verified.append(self._verified_step(working, outcome))
                    outcomes.append(outcome)
                    done = True
                    break

                if self.on_off_track is not None:
                    self.on_off_track(working, outcome)

                # Budget check BEFORE thinking — the old app's
                # `attempts.length >= maxThinks` gate.
                if len(attempts) >= self.max_thinks or self.llm is None:
                    failed_step = working
                    failure_reason = (
                        f"budget exhausted: {_off_track_reason(working, outcome)}"
                        if len(attempts) >= self.max_thinks
                        else f"no LLM available to repair: {_off_track_reason(working, outcome)}"
                    )
                    outcomes.append(outcome)
                    done = True
                    break

                # Repair-cache fast path (engine optimization; the old app has
                # no cache — a cache hit must reproduce the same repair the
                # LLM produced before, so outcomes never diverge).
                if self.action_cache is not None and self.golden_script_id is not None:
                    cache_key = self._cache_key_for(working)
                    cached = self.action_cache.get(self.golden_script_id, step_index=working.index, cache_key=cache_key)
                    if cached:
                        try:
                            cached_outcome = run_step(
                                self.page, working, selector=cached[0]["selector"],
                                timeout_ms=working.wait_ceiling_ms or 15000, shot_dir=shot_dir,
                            )
                            if cached_outcome.on_track:
                                verified.append(self._verified_step(working, cached_outcome))
                                outcomes.append(cached_outcome)
                                done = True
                                break
                        except Exception:
                            pass
                        self.action_cache.record_miss(self.golden_script_id, step_index=working.index, cache_key=cache_key)

                repair = think(self.llm, working, self.page, outcome, attempts=attempts, goal=goal)
                thinks.append({"step": working.index, "action": repair.action, "reason": repair.reason})
                act_key = json.dumps(dataclasses.asdict(repair), default=str)
                if any(a.endswith(f"::{act_key}") for a in attempts):
                    attempts.append(
                        f"REJECTED: you proposed EXACTLY this before and it did not fix the step — propose something DIFFERENT ::{act_key}"
                    )
                    continue

                if repair.action in ("give_up", "declare_blocker"):
                    attempts.append(f"{repair.action}: {repair.reason or ''}")
                    failed_step = working
                    failure_reason = (
                        f"blocker: {repair.kind} — {repair.evidence}"
                        if repair.action == "declare_blocker"
                        else f"give_up: {repair.reason}"
                    )
                    outcomes.append(outcome)
                    done = True
                    break

                if repair.action == "reexec_prev":
                    # Re-run the PREVIOUS step (a silently failed prior action),
                    # then retry the current one — the old app re-executes
                    # prevIdx (best-effort, never verified) and lets the loop
                    # retry this step. No previous step → REJECTED feedback.
                    act_key = json.dumps(dataclasses.asdict(repair), default=str)
                    attempts.append(f"(applied) ::{act_key}")
                    if pos > 0:
                        try:
                            self._execute(steps[pos - 1], shot_dir)
                        except Exception:
                            pass
                        attempts.append("reexec_prev")
                    else:
                        attempts.append("reexec_prev — REJECTED: no previous step")
                    continue

                if repair.action == "remove_step":
                    # Drop the entry — it is never verified, never emitted.
                    attempts.append(f"remove_step ({repair.reason}) — recorded action was a fumble/no-op")
                    steps.pop(pos)
                    done = True
                    pos -= 1
                    break

                if repair.action == "insert_step":
                    count, _visible = resolve_count(self.page, repair.selector)
                    if count == 0:
                        attempts.append(f"insert_step {repair.selector} — REJECTED: matches nothing")
                        continue
                    expr = _simple_selector_to_expr(repair.selector)
                    insert_seq += 1
                    new_step = TrajectoryStep(
                        index=working.index - 1 + insert_seq * 0.001,
                        kind=repair.gesture if repair.gesture in ("click", "hover", "dblclick", "hover_click") else "click",
                        selector_candidates=[{"selector": expr, "rank": 0}],
                        value=None,
                        expected_effect="none",
                        wait_ceiling_ms=30000,
                    )
                    attempts.append(f"insert_step {expr}")
                    # Execute the inserted step NEXT (the current one is retried
                    # after), exactly like the old app's order.splice(pos, 0, new).
                    steps.insert(pos, new_step)
                    done = True
                    pos -= 1
                    break

                working, applied = self._apply_repair(working, repair, attempts)
                if applied:
                    outcome = self._execute(working, shot_dir)
                    if outcome.download_saved:
                        downloads.append(outcome.download_saved)
                    if outcome.on_track:
                        if repair.action == "replace_selector" and self.action_cache is not None and self.golden_script_id is not None and cache_key:
                            self._put_cache(working, cache_key, outcome)
                        verified.append(self._verified_step(working, outcome))
                        outcomes.append(outcome)
                        done = True
                        break

            pos += 1

        return BrainLoopResult(
            outcomes,
            trajectory=ReferenceTrajectory(steps=tuple(verified)),
            thinks=thinks,
            failed_step=failed_step,
            failure_reason=failure_reason,
            downloads=downloads,
        )

    def _execute(self, step: TrajectoryStep, shot_dir: str | None) -> StepOutcome:
        return run_step(
            self.page, step,
            force=step.force_click, shot_dir=shot_dir,
            x=step.x, y=step.y, key=step.key,
        )

    def _verified_step(self, working: TrajectoryStep, outcome: StepOutcome) -> TrajectoryStep:
        """The step as it will be frozen into the golden: repairs folded in as
        the rank-0 selector, plus the live-resolved element identity. Mirrors
        the old app's VerifiedEntry (usedLocator + resolvedIdentity)."""
        resolved = outcome.resolved_selector
        top = working.selector_candidates[0]["selector"] if working.selector_candidates else None
        if resolved and resolved != top:
            working = dataclasses.replace(
                working,
                selector_candidates=[{"selector": resolved, "rank": 0}, *working.selector_candidates],
            )
        if outcome.resolved_identity:
            working = dataclasses.replace(working, identity=outcome.resolved_identity)
        return working

    def _cache_key_for(self, step: TrajectoryStep) -> str:
        original_selector = step.selector_candidates[0]["selector"] if step.selector_candidates else ""
        instruction = f"{step.kind} {original_selector}"
        url_pattern = urlparse(self.page.url).path
        try:
            dom = self.page.content()
        except Exception:
            dom = ""
        dom_fingerprint = hashlib.sha256(sanitize_dom(dom).encode("utf-8")).hexdigest()
        return compute_cache_key(instruction=instruction, url_pattern=url_pattern, dom_fingerprint=dom_fingerprint)

    def _put_cache(self, step: TrajectoryStep, cache_key: str, outcome: StepOutcome) -> None:
        if self.action_cache is None or self.golden_script_id is None or cache_key is None:
            return
        self.action_cache.put(
            self.golden_script_id, step_index=step.index, cache_key=cache_key,
            selector_candidates=[{"selector": outcome.resolved_selector, "rank": 0}], method=step.kind,
        )

    def _apply_repair(
        self, step: TrajectoryStep, repair: RepairAction, attempts: list[str],
    ) -> tuple[TrajectoryStep, bool]:
        """Applies one validated correction to the step, feeding any rejection
        back into `attempts` so the next think round sees it. Returns
        (mutated step, whether to re-execute)."""
        act_key = json.dumps(dataclasses.asdict(repair), default=str)
        attempts.append(f"(applied) ::{act_key}")

        if repair.action == "replace_selector":
            count, _visible = resolve_count(self.page, repair.selector)
            if count == 0:
                attempts.append(f"replace_selector {repair.selector} — REJECTED: matches nothing in the live DOM")
                return step, False
            expr = _simple_selector_to_expr(repair.selector)
            attempts.append(f"replace_selector {repair.selector} (matched {count}, visible {_visible})")
            return dataclasses.replace(
                step,
                selector_candidates=[{"selector": expr, "rank": 0}, *step.selector_candidates],
            ), True

        if repair.action == "set_gesture":
            if repair.gesture not in ("click", "hover_click", "hover", "dblclick", "dispatch_click", "type", "fill", "press", "check", "uncheck", "select_option"):
                attempts.append(f"set_gesture {repair.gesture} — REJECTED: unknown gesture")
                return step, False
            return dataclasses.replace(step, kind=repair.gesture, key=repair.key), True

        if repair.action == "force_click":
            return dataclasses.replace(step, force_click=True), True

        if repair.action == "wait_longer":
            ceiling = min(max(repair.ceiling_ms or 120000, 30000), 600000)
            return dataclasses.replace(step, wait_ceiling_ms=ceiling), True

        if repair.action == "set_value":
            return dataclasses.replace(step, value=repair.value), True

        if repair.action == "click_coordinates":
            return dataclasses.replace(step, kind="click_xy", x=repair.x, y=repair.y), True

        if repair.action == "dismiss":
            # Best-effort click on the unexpected interstitial — no validation
            # gate, the old app just reports clicked / not visible and retries.
            count, _visible = resolve_count(self.page, repair.selector)
            ok = count > 0
            if ok:
                try:
                    run_step(
                        self.page,
                        TrajectoryStep(
                            step.index, "click",
                            [{"selector": _simple_selector_to_expr(repair.selector), "rank": 0}],
                            None, "none",
                        ),
                        timeout_ms=8000, shot_dir=None,
                    )
                except Exception:
                    ok = False
            attempts.append(f"dismiss {repair.selector} → {'clicked' if ok else 'not visible'}")
            return step, True

        return step, False