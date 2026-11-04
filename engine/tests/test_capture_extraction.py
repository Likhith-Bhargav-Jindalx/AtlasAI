import json

from playwright.sync_api import sync_playwright


def test_collect_page_data_extracts_table_and_labeled_fields(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/extraction_target.html")

        from atlas_engine.capture.extraction import collect_page_data
        data = collect_page_data(page)

        browser.close()

    assert data["fields"]["mrn"] == "MRN-4821"
    assert data["fields"]["ward"] == "West Wing 3"
    assert data["fields"]["date_of_birth"] == "1990-01-15"

    assert len(data["tables"]) == 1
    table = data["tables"][0]
    assert table["name"] == "visits_table"
    assert table["headers"] == ["Date", "Reason"]
    assert table["rows"] == [["2026-08-01", "Checkup"], ["2026-07-15", "Follow-up"]]


def test_sanitize_collected_text_scrubs_lone_surrogates_and_nul_bytes():
    from atlas_engine.capture.extraction import sanitize_collected_text

    payload = {
        "fields": {"mrn": "MRN-\ud8004821", "note": "before\x00after"},
        "tables": [{"name": "t", "headers": ["a\ud800"], "rows": [["x\x00y", True, 3]]}],
    }

    clean = sanitize_collected_text(payload)

    # Postgres rejects both of these in a JSONB value; nothing may survive.
    assert "\x00" not in json.dumps(clean)
    json.dumps(clean).encode("utf-8")  # would raise UnicodeEncodeError on a lone surrogate
    assert clean["fields"]["mrn"] == "MRN-?4821"
    assert clean["fields"]["note"] == "beforeafter"
    assert clean["tables"][0]["headers"] == ["a?"]
    # Non-string leaves are passed through untouched.
    assert clean["tables"][0]["rows"] == [["xy", True, 3]]


def test_collect_page_data_survives_a_malformed_string_in_the_page(fixture_portal_url):
    """A lone UTF-16 surrogate or NUL byte set from JS must not make it into
    the collected payload — persist_recording inserts extractions in the same
    transaction as the Recording, so a value Postgres refuses would take the
    whole recording down with it."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/extraction_target.html")
        page.evaluate(
            "() => {"
            "  document.querySelector('[data-lbl=\"MRN\"]').textContent ="
            "    'MRN-' + String.fromCharCode(0xD800) + '48' + String.fromCharCode(0) + '21';"
            "}"
        )

        from atlas_engine.capture.extraction import collect_page_data
        data = collect_page_data(page)

        browser.close()

    mrn = data["fields"]["mrn"]
    assert "\x00" not in mrn
    assert "MRN-" in mrn and "21" in mrn
    # Serializable and encodable — i.e. the driver would accept it.
    json.dumps(data).encode("utf-8")


def test_attach_extraction_button_notifies_on_click(fixture_portal_url):
    from atlas_engine.capture.extraction import attach_extraction_button

    requested_pages = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        attach_extraction_button(context, requested_pages.append)
        page = context.new_page()
        page.goto(f"{fixture_portal_url}/extraction_target.html")

        page.click("#__atlasGrabBtn")
        page.wait_for_function("document.getElementById('__atlasGrabBtn').textContent.includes('captured')")

        browser.close()

    assert requested_pages == [page]


def test_atlas_grab_btn_is_draggable_and_a_drag_does_not_trigger_extraction(fixture_portal_url):
    """A real portal's own fixed UI (or the native Playwright codegen
    overlay, confirmed live) can end up covering this button's default
    corner -- the operator must be able to drag it clear, and that drag
    must not also fire an extraction (it's not a click)."""
    from atlas_engine.capture.extraction import attach_extraction_button

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        attach_extraction_button(context, lambda _page: None)
        page = context.new_page()
        page.goto(f"{fixture_portal_url}/extraction_target.html")
        page.wait_for_selector("#__atlasGrabBtn")

        before = page.eval_on_selector("#__atlasGrabBtn", "el => el.getBoundingClientRect()")
        box = page.locator("#__atlasGrabBtn").bounding_box()
        start_x, start_y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        page.mouse.move(start_x, start_y)
        page.mouse.down()
        page.mouse.move(start_x - 200, start_y + 150, steps=10)
        page.mouse.up()
        after = page.eval_on_selector("#__atlasGrabBtn", "el => el.getBoundingClientRect()")
        # Still shows the original label -- a drag never started an extraction.
        label_after_drag = page.text_content("#__atlasGrabBtn")

        browser.close()

    assert (after["left"], after["top"]) != (before["left"], before["top"])
    assert "Extract this page" in label_after_drag


# The optional, always-visible "why this step?" corner note box (and its
# window.__atlas_note binding) was replaced by a mandatory per-action modal
# (see recorder.py's _LISTENER_SCRIPT/showPendingNoteModal and
# test_capture_recorder.py's test_capture_session_* note/modal tests) that
# targets the action still in flight correctly, rather than whatever was
# last finalized.
