"""Tests for run_step's download handling. Some real portals (confirmed
live against eCW's BI report export) trigger the actual file download from
a DIFFERENT page than the one the click was dispatched on — a new/hidden
tab, not the page being watched. Scoping the wait to page.expect_download()
misses it entirely (a real TimeoutError even though the file genuinely
downloads), because expect_download() only listens on that one page.
context.expect_event("download", ...) catches it regardless of which page
in the browser context fired it."""

from playwright.sync_api import sync_playwright

from atlas_engine.brain.loop import run_step
from atlas_engine.brain.models import TrajectoryStep

# Clicking #go, all synchronously within the ONE click run_step dispatches:
# opens a second page, writes a download link into it, and clicks that link
# — the download fires on the NEW page, never on the page that was clicked.
_TRIGGER_PAGE_HTML = """
<html><body>
<button id="go" onclick="
  var w = window.open('about:blank', '_blank');
  w.document.write('&lt;a id=&quot;dl&quot; download=&quot;report.txt&quot; href=&quot;data:text/plain;base64,aGVsbG8=&quot;&gt;dl&lt;/a&gt;');
  w.document.getElementById('dl').click();
">Go</button>
</body></html>
"""


def test_download_is_captured_when_it_fires_on_the_same_page(tmp_path):
    """The common case — regression check that switching to a context-scoped
    listener didn't break the simple, same-page download."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_content(
            '<a id="dl" download="report.txt" '
            'href="data:text/plain;base64,aGVsbG8=">download</a>'
        )

        step = TrajectoryStep(
            index=0, kind="click",
            selector_candidates=[{"selector": "#dl", "rank": 0}],
            value=None, expected_effect="none", expects_download=True,
        )
        outcome = run_step(page, step, timeout_ms=5000, shot_dir=str(tmp_path))

        browser.close()

    assert outcome.download_started is True
    assert outcome.download_saved is not None
    assert outcome.error is None


def test_download_is_captured_even_when_it_fires_on_a_different_page():
    """Reproduces the eCW report-export pattern: the click opens (or
    already has open) a second page, and THAT page is what actually fires
    the browser's download event — not the page the click was dispatched
    on. run_step must still detect and save the download."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_content(_TRIGGER_PAGE_HTML)

        step = TrajectoryStep(
            index=0, kind="click",
            selector_candidates=[{"selector": "#go", "rank": 0}],
            value=None, expected_effect="none", expects_download=True,
        )
        outcome = run_step(page, step, timeout_ms=5000)

        browser.close()

    assert outcome.download_started is True
    assert outcome.error is None
