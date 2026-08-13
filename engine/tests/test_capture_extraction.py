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
