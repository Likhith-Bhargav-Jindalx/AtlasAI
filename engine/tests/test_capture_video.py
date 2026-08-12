import shutil

from playwright.sync_api import sync_playwright

from atlas_engine.capture.video import finalize_video, start_capture_context


def test_start_and_finalize_video_produces_a_playable_file(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context, page, started_at_ms, video_dir = start_capture_context(browser)
        try:
            page.goto(f"{fixture_portal_url}/login.html")
            page.fill("#username", "demo")

            video_path, ended_at_ms = finalize_video(context, page)
            browser.close()

            assert video_path is not None
            assert video_path.exists()
            assert video_path.stat().st_size > 0
            assert ended_at_ms >= started_at_ms
        finally:
            shutil.rmtree(video_dir, ignore_errors=True)


def test_video_path_for_page_does_not_close_anything(fixture_portal_url):
    from atlas_engine.capture.video import video_path_for_page

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context, page, _started_at_ms, video_dir = start_capture_context(browser)
        try:
            page.goto(f"{fixture_portal_url}/login.html")

            path = video_path_for_page(page)

            assert path is not None
            # Context/page must still be usable — nothing was closed.
            assert page.title() is not None

            context.close()
            browser.close()
        finally:
            shutil.rmtree(video_dir, ignore_errors=True)


def test_finalize_video_tolerates_a_context_that_is_already_closed(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context, page, started_at_ms, video_dir = start_capture_context(browser)
        try:
            page.goto(f"{fixture_portal_url}/login.html")
            context.close()  # simulates the browser having already gone away

            # Must not raise even though the context is already closed.
            video_path, ended_at_ms = finalize_video(context, page)

            assert ended_at_ms >= started_at_ms
            browser.close()
        finally:
            shutil.rmtree(video_dir, ignore_errors=True)
