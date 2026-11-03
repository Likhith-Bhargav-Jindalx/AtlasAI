from playwright.sync_api import sync_playwright

from atlas_engine.brain.evaluate import StepOutcome, evaluate
from atlas_engine.brain.models import TrajectoryStep
from atlas_engine.brain.repair import RepairAction, _build_user_text, think
from atlas_engine.llm.client import FakeLlmClient


def _outcome(step_index=0, **overrides):
    defaults = dict(
        step_index=step_index, on_track=False, url_after="https://x/a",
        dom_after="<a/>", resolved_selector="#a",
    )
    defaults.update(overrides)
    return StepOutcome(**defaults)


def test_build_user_text_includes_the_operator_note_when_present():
    """operator_note is grounding context the repair LLM doesn't have
    today -- why the human took this action, captured live during
    recording. Present -> surfaced in the prompt; absent -> no new noise."""
    step = TrajectoryStep(
        0, "click", [{"selector": "#submit", "rank": 0}], None, "dom_changed",
        operator_note="Clicking submit even though the amount looks wrong, per payer instructions",
    )
    text = _build_user_text(step, "<html/>", _outcome(), attempts=[])
    assert "Clicking submit even though the amount looks wrong, per payer instructions" in text


def test_build_user_text_omits_operator_note_section_when_absent():
    step = TrajectoryStep(0, "click", [{"selector": "#submit", "rank": 0}], None, "dom_changed")
    text = _build_user_text(step, "<html/>", _outcome(), attempts=[])
    assert "operator note" not in text.lower()


def test_brain_prompt_uses_the_executor_and_golden_select_option_action_name():
    """The model-facing vocabulary must match the name accepted by
    BrainLoop and emitted into Playwright; `select` was a stale internal name
    from the previous TypeScript trajectory format."""
    from atlas_engine.brain.repair import _GESTURES, _SYSTEM_PROMPT

    assert "select_option" in _GESTURES
    assert "select_option" in _SYSTEM_PROMPT
    assert '|select"' not in _SYSTEM_PROMPT


def test_think_validates_and_returns_a_selector_that_exists_on_the_live_page(fixture_portal_url):
    step = TrajectoryStep(
        index=0, kind="click", selector_candidates=[{"selector": "#does-not-exist", "rank": 0}],
        value=None, expected_effect="dom_changed",
    )
    fake_llm = FakeLlmClient(responses=[{"action": "replace_selector", "selector": "#generate-report-btn"}])

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/dashboard.html")

        outcome = evaluate(
            step, url_before=page.url, url_after=page.url,
            dom_before=page.content(), dom_after=page.content(),
            resolved_selector="#does-not-exist",
        )
        result = think(fake_llm, step, page, outcome)

        browser.close()

    assert result == RepairAction(action="replace_selector", selector="#generate-report-btn")
    assert len(fake_llm.calls) == 1


class _RaisingLlmClient:
    def complete(self, *args, **kwargs):
        raise RuntimeError("Client error '403 Forbidden' for url 'https://x.openai.azure.com/...'")


def test_think_surfaces_the_real_exception_instead_of_a_misleading_non_json_claim(fixture_portal_url):
    """Confirmed via a real Azure 403 (a VNet-restricted resource): the old
    bare `except Exception: return give_up('brain returned non-JSON')`
    relabeled EVERY failure -- network errors, HTTP errors, auth errors,
    not just an actual JSON parse error -- with the same false claim,
    making the real cause undiagnosable from the authoring run's own logs."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        step = TrajectoryStep(0, "click", [{"selector": "#login-btn", "rank": 0}], None, "dom_changed")
        result = think(_RaisingLlmClient(), step, page, _outcome())

        browser.close()

    assert result.action == "give_up"
    assert "403 Forbidden" in result.reason


def test_think_returns_the_proposal_unchanged_even_when_the_selector_matches_nothing(fixture_portal_url):
    """think() proposes; the LOOP validates. A proposal whose selector matches
    nothing on the live page is returned as-is and rejected by the loop (re-fed
    as REJECTED feedback), exactly like the old app — not coerced to give_up."""
    step = TrajectoryStep(
        index=0, kind="click", selector_candidates=[{"selector": "#does-not-exist", "rank": 0}],
        value=None, expected_effect="dom_changed",
    )
    fake_llm = FakeLlmClient(responses=[{"action": "replace_selector", "selector": "#also-does-not-exist"}])

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/dashboard.html")

        outcome = evaluate(
            step, url_before=page.url, url_after=page.url,
            dom_before=page.content(), dom_after=page.content(),
            resolved_selector="#does-not-exist",
        )
        result = think(fake_llm, step, page, outcome)

        browser.close()

    assert result == RepairAction(action="replace_selector", selector="#also-does-not-exist")


def test_brain_loop_rejects_a_selector_that_does_not_match_then_repairs_with_the_next(fixture_portal_url):
    """The loop resolve-counts each proposed selector before applying it — a
    proposal that matches nothing is re-fed as REJECTED feedback and the next
    proposal is tried."""
    from atlas_engine.brain.loop import BrainLoop
    from atlas_engine.brain.models import ReferenceTrajectory

    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "click", [{"selector": "#stale-selector", "rank": 0}], None, "dom_changed"),
        )
    )
    fake_llm = FakeLlmClient(
        responses=[
            {"action": "replace_selector", "selector": "#also-does-not-exist"},
            {"action": "replace_selector", "selector": "#generate-report-btn"},
        ]
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/dashboard.html")

        outcomes = BrainLoop(page, llm=fake_llm).run(trajectory)

        browser.close()

    assert outcomes[0].on_track is True
    assert len(fake_llm.calls) == 2
    # The verified trajectory carries the selector that actually worked.
    assert outcomes.trajectory.steps[0].selector_candidates[0]["selector"] == "#generate-report-btn"


def test_brain_loop_repairs_a_broken_selector_and_the_retry_succeeds(fixture_portal_url):
    from atlas_engine.brain.loop import BrainLoop
    from atlas_engine.brain.models import ReferenceTrajectory

    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "click", [{"selector": "#stale-selector", "rank": 0}], None, "dom_changed"),
        )
    )
    fake_llm = FakeLlmClient(responses=[{"action": "replace_selector", "selector": "#generate-report-btn"}])

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/dashboard.html")

        outcomes = BrainLoop(page, llm=fake_llm).run(trajectory)

        browser.close()

    assert outcomes[0].on_track is True
    assert len(fake_llm.calls) == 1
