import time

import pytest
from playwright.sync_api import sync_playwright

from atlas_engine.capture.extraction import attach_extraction_button
from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.trajectory.extract import expected_effect_for_action


def _confirm_note(page) -> None:
    """Dismisses the mandatory note modal with its pre-filled default —
    every real click in these tests shows one, and it blocks any further
    click from reaching its real target until confirmed. Fills never show
    this modal."""
    page.wait_for_selector("#__atlasNoteModal input")
    page.keyboard.press("Enter")
    page.wait_for_selector("#__atlasNoteModal", state="detached")


def test_capture_session_records_click_and_fill(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.fill("#username", "demo")
            page.fill("#password", "demo-pass")
            page.click("#login-btn")
            page.wait_for_url("**/dashboard.html")

        browser.close()

    kinds = [action.kind for action in capture.actions]
    assert kinds == ["fill", "fill", "click"]

    username_fill = capture.actions[0]
    assert username_fill.value == "demo"
    assert any(candidate["selector"] == "#username" for candidate in username_fill.selector_candidates)

    login_click = capture.actions[2]
    assert login_click.url_before.endswith("/login.html")
    assert "welcome" in login_click.dom_after or "Welcome" in login_click.dom_after


def test_capture_session_records_password_value_for_later_scrubbing(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.fill("#password", "demo-pass")

        browser.close()

    password_fill = capture.actions[0]
    assert password_fill.value == "demo-pass"
    assert any(c["selector"] == "#password" for c in password_fill.selector_candidates)


def test_exit_after_browser_disconnected_finalizes_without_querying_the_page(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        capture = CaptureSession(page)
        capture.__enter__()
        page.fill("#username", "demo")

        # The fill's "input" event is captured client-side and relayed to
        # _on_event via an exposed binding — a real CDP round-trip that
        # page.fill() itself does not wait for (it only waits for the DOM
        # value to be set and the in-browser event to dispatch). Every other
        # test in this file reads capture.actions only after __exit__'s
        # NORMAL path, whose wait_for_load_state call incidentally flushes
        # that round-trip first — this test takes the disconnected-path
        # branch instead, which has no such synchronization, so it must
        # poll for the pending action to actually land or it flakes under
        # load (observed in CI) rather than reliably "leaving one action
        # pending" the instant fill() returns.
        deadline = time.time() + 5
        while capture._pending is None and time.time() < deadline:
            page.wait_for_timeout(50)
        assert capture._pending is not None  # leaves one action pending

        capture.mark_browser_disconnected()
        assert capture.browser_disconnected is True

        # Must not hang or raise trying to query a page we're pretending is
        # already gone — and must not need the page to still work at all.
        capture.__exit__(None, None, None)

        browser.close()

    assert len(capture.actions) == 1
    action = capture.actions[0]
    assert action.url_after == action.url_before
    assert action.dom_after == action.dom_before


def test_exit_falls_back_gracefully_when_the_page_died_without_being_marked_disconnected(fixture_portal_url):
    """Defense-in-depth for the same race _wait_for_stop_or_disconnect
    guards against: even if something else ever lets __exit__ run against
    an already-dead page (browser_disconnected still False), it must fall
    back to finalizing from the pending state instead of letting a live
    Playwright call raise/hang and abort the whole capture session."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        capture = CaptureSession(page)
        capture.__enter__()
        page.fill("#username", "demo")

        deadline = time.time() + 5
        while capture._pending is None and time.time() < deadline:
            page.wait_for_timeout(50)
        assert capture._pending is not None  # leaves one action pending

        page.close()  # the page dies for real, but browser_disconnected is never set
        assert capture.browser_disconnected is False

        capture.__exit__(None, None, None)  # must not raise

        browser.close()

    assert len(capture.actions) == 1
    action = capture.actions[0]
    assert action.url_after == action.url_before
    assert action.dom_after == action.dom_before


def test_action_timestamp_reflects_when_the_event_fired_not_when_it_was_finalized(fixture_portal_url):
    """timestamp_ms drives the cross-tab merge sort in recording_service.py
    (_merge_actions_for_trajectory) -- if it were stamped at finalization
    instead of at the original event, an action that sits pending across a
    long gap (e.g. the user switched tabs) would sort AFTER actions that
    actually happened later but finalized sooner, corrupting the merged
    order. Confirmed as a real bug via a live multi-tab recording against
    a real external site before this test was written."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.fill("#username", "demo")  # fires now, becomes pending
            fired_at_ms = int(time.time() * 1000)
            page.wait_for_timeout(300)  # a real gap before anything finalizes it
            page.fill("#password", "demo-pass")  # finalizes the username fill, ~300ms later

        browser.close()

    username_fill = capture.actions[0]
    assert username_fill.timestamp_ms < fired_at_ms + 150  # nowhere near +300ms later


def test_capture_session_ignores_clicks_on_the_extraction_button(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        grabbed_pages = []
        attach_extraction_button(context, grabbed_pages.append)

        page.goto(f"{fixture_portal_url}/login.html")
        page.wait_for_selector("#__atlasGrabBtn")

        with CaptureSession(page) as capture:
            page.click("#__atlasGrabBtn")
            page.fill("#username", "demo")  # a real action, to prove capture still works

        browser.close()

    # The button click really happened (it notified Python)...
    assert grabbed_pages == [page]
    # ...but it must never show up as a recorded trajectory step, since the
    # button only exists during live capture and can never be replayed.
    assert not any(
        candidate["selector"] == "#__atlasGrabBtn"
        for action in capture.actions
        for candidate in action.selector_candidates
    )
    assert [action.kind for action in capture.actions] == ["fill"]


def test_recorded_dom_snapshots_exclude_the_extraction_button(fixture_portal_url):
    """The button's label mutates while a grab is in flight ("⏳ extracting…"
    → "✓ …" → reverts after 2.5s). If it were part of the DOM snapshots, a
    neighbouring *real* action whose before/after straddled that window would
    be misclassified as dom_changed by the verbatim diff in
    trajectory/extract.py — and read as off-track on replay, where the button
    does not exist at all."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        attach_extraction_button(context, lambda _page: None)

        page.goto(f"{fixture_portal_url}/login.html")
        page.wait_for_selector("#__atlasGrabBtn")

        with CaptureSession(page) as capture:
            page.click("#__atlasGrabBtn")  # starts the label mutation
            page.fill("#username", "demo")  # a real action, mid-mutation
            page.fill("#password", "demo-pass")  # finalizes the username fill

        browser.close()

    username_fill = capture.actions[0]
    assert username_fill.kind == "fill"
    assert "__atlasGrabBtn" not in username_fill.dom_before
    assert "__atlasGrabBtn" not in username_fill.dom_after
    # A fill sets the value property, not markup — so with the button gone
    # from both snapshots the action's true effect is visible as "none".
    assert expected_effect_for_action(username_fill) == "none"


def test_capture_session_attaches_a_pending_note_to_the_action_still_in_flight(fixture_portal_url):
    """A note submitted via __atlas_pending_note (the mandatory modal's
    binding) attaches to the action still _pending — the one the operator
    was just prompted about — not to whatever was last finalized. This is
    the opposite targeting from the old optional note box, which could only
    ever reach an action already one event behind."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.click("#username")  # becomes pending, its mandatory modal appears
            page.evaluate("() => window.__atlas_pending_note('per payer instructions')")
            # Bypassing the modal's own UI (unlike a real operator) to target
            # the binding directly -- still must dismiss it exactly as a real
            # confirm would, or it blocks the next click like any other.
            page.evaluate("() => document.getElementById('__atlasNoteModal')?.remove()")
            page.click("#password")  # finalizes the #username click, now carrying the note

        browser.close()

    assert [a.operator_note for a in capture.actions] == ["per payer instructions", None]


def test_capture_session_ignores_a_pending_note_submitted_when_nothing_is_pending(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.evaluate("() => window.__atlas_pending_note('too early')")
            page.click("#username")

        browser.close()

    assert [a.operator_note for a in capture.actions] == [None]


def test_capture_session_does_not_show_a_note_modal_for_a_click_that_only_focuses_a_field(fixture_portal_url):
    """Reproduced via a real recording: clicking a search box to start
    typing immediately popped the mandatory modal, which steals focus --
    the operator could never actually type a single letter without first
    confirming a note for the mere act of clicking the field, so they kept
    re-clicking it instead. A click into an INPUT/TEXTAREA/SELECT is just
    setup for the fill that follows (fills never show this modal at all)
    and is not itself a meaningful action."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.click("#username")
            page.wait_for_timeout(200)
            assert page.locator("#__atlasNoteModal").count() == 0
            page.type("#username", "demo")  # now able to actually type
            page.wait_for_timeout(200)
            assert page.locator("#__atlasNoteModal").count() == 0

        browser.close()

    # The focus-only click is still recorded (unchanged, pre-existing
    # behavior) -- only the mandatory modal is skipped for it. page.type()
    # fires one "input" per keystroke, each its own fill action (matching
    # real per-keystroke recording); none of them ever show a modal.
    kinds = [a.kind for a in capture.actions]
    assert kinds == ["click"] + ["fill"] * (len(kinds) - 1)
    assert capture.actions[0].operator_note is None
    assert capture.actions[-1].value == "demo"


def test_capture_session_shows_a_mandatory_note_modal_after_a_click_with_a_mechanical_default(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.click("#login-btn")
            modal_input = page.locator("#__atlasNoteModal input")
            modal_input.wait_for(state="visible")
            assert modal_input.input_value() == ""  # not prefilled
            assert modal_input.get_attribute("placeholder") is None  # not even shown as a hint
            default_text = modal_input.get_attribute("data-default-text")
            assert default_text  # non-empty mechanical default, no LLM call
            page.keyboard.press("Enter")
            page.click("#username")  # finalizes the login-btn click, carrying its note

        browser.close()

    assert capture.actions[0].operator_note == default_text


def test_capture_session_does_not_show_a_note_modal_for_a_page_triggered_click(fixture_portal_url):
    """A real portal's own script can click a button programmatically --
    auto-advance, a hidden trigger, a restore-draft action -- with no human
    anywhere near it. The modal must not pop for that; only a real,
    trusted click should ever prompt the operator."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.evaluate("document.getElementById('login-btn').click()")
            page.wait_for_timeout(300)
            assert page.locator("#__atlasNoteModal").count() == 0

        browser.close()

    assert capture.actions[0].kind == "click"


def test_capture_session_does_not_show_a_note_modal_for_a_page_triggered_input(fixture_portal_url):
    """Same as above for a programmatic value + dispatched 'input' event --
    e.g. browser autofill, a restore-draft script, or a framework
    re-rendering a controlled input. No real typing happened, so no modal."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.evaluate(
                "const el = document.getElementById('username');"
                "el.value = 'restored-draft';"
                "el.dispatchEvent(new Event('input', {bubbles: true}));"
            )
            page.wait_for_timeout(300)
            assert page.locator("#__atlasNoteModal").count() == 0

        browser.close()

    assert capture.actions[0].value == "restored-draft"


def test_capture_session_note_modal_blocks_a_subsequent_click_until_confirmed(fixture_portal_url):
    """Proves the overlay is genuinely blocking, not cosmetic: a second real
    click attempted while the modal from the first is still open must fail
    to reach its target (Playwright's actionability check raises) rather
    than silently landing on the overlay."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.click("#login-btn")
            page.wait_for_selector("#__atlasNoteModal")
            with pytest.raises(Exception):
                page.click("#username", timeout=1000)

        browser.close()


def test_capture_session_never_shows_a_note_modal_for_typing(fixture_portal_url):
    """A real operator typing into a field fires one 'input' event per
    keystroke (see the real multi-keystroke trajectories this session
    captured) -- a modal on every keystroke (or even once per field) made
    typing unusable and popped up unexpectedly mid-flow, so fills no longer
    ever trigger the mandatory modal; only clicks do."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.locator("#username").press_sequentially("demo", delay=30)
            page.wait_for_timeout(1500)
            assert page.locator("#__atlasNoteModal").count() == 0

        browser.close()

    assert [a.value for a in capture.actions if a.kind == "fill"][-1] == "demo"


def test_capture_session_use_default_button_fills_the_box_without_confirming(fixture_portal_url):
    """Use Default only populates the input with the mechanical default,
    replacing whatever was typed -- it never confirms by itself, so the
    default text is only ever shown once the operator explicitly asks for
    it, and Continue is still required to actually submit the note."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.click("#login-btn")
            modal_input = page.locator("#__atlasNoteModal input")
            modal_input.wait_for(state="visible")
            default_text = modal_input.get_attribute("data-default-text")
            modal_input.fill("something the operator typed instead")
            page.click("#__atlasNoteModalDefaultBtn")
            assert modal_input.input_value() == default_text  # box now shows it
            assert page.locator("#__atlasNoteModal").count() == 1  # still open, not confirmed
            page.click("#__atlasNoteModalContinueBtn")
            page.wait_for_selector("#__atlasNoteModal", state="detached")
            page.click("#username")  # finalizes the login-btn click, carrying its note

        browser.close()

    assert capture.actions[0].operator_note == default_text


def test_capture_session_undoes_the_most_recently_finalized_action(fixture_portal_url):
    """Undo always targets 'whatever Python currently considers the last
    FINALIZED action' -- no JS-side index to go stale across a navigation
    (see recorder.py's _LISTENER_SCRIPT comment on why an arbitrary-index
    remove list was dropped in favor of this)."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.click("#username")  # becomes pending -- no modal, it's a field focus click
            page.click("#password")  # finalizes #username; becomes pending
            page.evaluate("() => window.__atlas_undo_last()")  # undoes #username

        browser.close()

    assert [a.kind for a in capture.actions] == ["click"]
    assert capture.actions[0].selector_candidates[0]["selector"] == "#password"


def test_capture_session_ignores_undo_before_any_action_is_finalized(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.evaluate("() => window.__atlas_undo_last()")  # nothing finalized yet -- no-op
            page.click("#username")

        browser.close()

    assert [a.kind for a in capture.actions] == ["click"]
    assert capture.actions[0].selector_candidates[0]["selector"] == "#username"


def test_undo_button_removes_the_most_recently_finalized_action(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.click("#username")  # becomes pending -- no modal, it's a field focus click
            page.click("#password")  # finalizes #username; becomes pending
            page.wait_for_selector("#__atlasUndoBtn")
            page.click("#__atlasUndoBtn")
            page.wait_for_function(
                "document.getElementById('__atlasUndoBtn').textContent.includes('removed')"
            )

        browser.close()

    assert [a.kind for a in capture.actions] == ["click"]
    assert capture.actions[0].selector_candidates[0]["selector"] == "#password"


def test_undo_button_shows_distinct_feedback_when_nothing_is_removed(fixture_portal_url):
    """A no-op undo (nothing finalized yet, or a repeat click re-targeting an
    already-removed action) must look different from a real removal -- the
    recording we checked where the operator clicked Undo 2-3 times showed no
    gap at all, and without this the button can't tell them why."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            # Nothing finalized yet -- clicking Undo must not claim "removed".
            page.click("#__atlasUndoBtn")
            page.wait_for_function(
                "document.getElementById('__atlasUndoBtn').textContent.includes('Nothing to undo')"
            )

            page.click("#username")  # becomes pending
            page.click("#password")  # finalizes #username; becomes pending
            page.click("#__atlasUndoBtn")  # real removal of #username
            page.wait_for_function(
                "document.getElementById('__atlasUndoBtn').textContent.includes('removed')"
            )
            page.wait_for_timeout(1600)  # let the label revert

            # Repeat click, nothing new finalized in between -- same no-op case.
            page.click("#__atlasUndoBtn")
            page.wait_for_function(
                "document.getElementById('__atlasUndoBtn').textContent.includes('Nothing to undo')"
            )

        browser.close()

    assert [a.kind for a in capture.actions] == ["click"]
    assert capture.actions[0].selector_candidates[0]["selector"] == "#password"


def test_undo_button_clicks_are_not_recorded_as_actions(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.click("#__atlasUndoBtn")
            page.fill("#username", "demo")  # a real action, to prove capture still works

        browser.close()

    assert [a.kind for a in capture.actions] == ["fill"]


def test_recorded_dom_snapshots_exclude_the_undo_button(fixture_portal_url):
    """Same bug class as the extraction button and note box: the button's
    own label mutates on click ('↶ Undo last step' -> '✓ removed'), so
    leaving it in the snapshot would make a real action's dom_before/
    dom_after differ on the button alone."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.click("#__atlasUndoBtn")  # starts the label mutation
            page.fill("#username", "demo")  # a real action, mid-mutation
            page.fill("#password", "demo-pass")  # finalizes the username fill

        browser.close()

    username_fill = capture.actions[0]
    assert "__atlasUndoBtn" not in username_fill.dom_before
    assert "__atlasUndoBtn" not in username_fill.dom_after


def test_capture_session_falls_back_to_a_text_selector_when_no_id_or_testid(fixture_portal_url):
    """Confirmed via real recordings against real sites (Wikipedia search
    results, a popup-trigger link): most real clickable elements have
    neither an id nor a data-testid, which left selector_candidates totally
    empty -- the step could never be resolved or acted on at all during
    replay, not even as an off-track attempt. A visible-text fallback gives
    the brain something to act on for the very common case of a link/button
    with real on-screen text."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")
        page.evaluate("""() => {
            const a = document.createElement('a');
            a.textContent = 'Forgot password?';
            a.href = '#';
            a.style.cssText = 'position:fixed;top:300px;left:300px';
            document.body.appendChild(a);
        }""")

        with CaptureSession(page) as capture:
            page.click("text=Forgot password?")
            _confirm_note(page)
            page.fill("#username", "demo")  # finalizes the click above

        browser.close()

    click = capture.actions[0]
    assert click.selector_candidates == [{"selector": "text=Forgot password?", "rank": 2}]


def test_capture_session_records_the_modifier_keys_held_during_a_click(fixture_portal_url):
    """Confirmed via a real multi-tab recording: a cmd+click is what makes a
    normal link (no target=_blank) open in a new tab instead of navigating
    in place. Without the modifier, replay performs a plain click and the
    popup it should open never opens at all -- 'Popup page N never opened'
    even though the step itself resolves and clicks fine."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.click("#login-btn", modifiers=["Meta"])
            page.wait_for_timeout(100)

        browser.close()

    assert capture.actions[0].modifiers == ["Meta"]


def test_capture_session_can_record_a_manually_opened_tab(fixture_portal_url):
    """A human opening a fresh tab (Cmd+T, or one already open when the
    recording starts) and navigating it isn't a DOM event -- nothing in
    _LISTENER_SCRIPT ever fires for it, so the caller (live_capture.py)
    must synthesize this as its own lifecycle action, not something
    CaptureSession infers from the page."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            capture.record_tab_opened("https://example.com/report", "<html></html>", origin="manual")
            page.click("#login-btn")

        browser.close()

    assert capture.actions[0].kind == "tab_opened"
    assert capture.actions[0].value == "https://example.com/report"
    assert capture.actions[0].tab_origin == "manual"
    assert capture.actions[1].kind == "click"


def test_capture_session_can_record_a_tab_close(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.click("#login-btn")
            capture.record_tab_closed()

        browser.close()

    assert capture.actions[-1].kind == "tab_closed"
    assert capture.actions[-1].value is None


def test_capture_session_records_no_modifiers_for_a_plain_click(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.click("#login-btn")
            page.wait_for_timeout(100)

        browser.close()

    assert capture.actions[0].modifiers == []
