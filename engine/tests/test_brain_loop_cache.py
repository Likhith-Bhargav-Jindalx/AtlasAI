import uuid

from playwright.sync_api import sync_playwright

from atlas_engine.brain.cache import ActionCacheStore
from atlas_engine.brain.loop import BrainLoop
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, Organization
from atlas_engine.db.repo import get_or_create_portal_profile
from atlas_engine.llm.client import FakeLlmClient


def _make_golden_script_id() -> uuid.UUID:
    with session_scope() as session:
        org = Organization(name="Cache Loop Org", slug=f"cache-loop-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), "cacheloop.example.com")
        golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py")
        session.add(golden)
        session.flush()
        return golden.id


def test_brain_loop_uses_cached_selector_before_calling_llm_again(fixture_portal_url):
    golden_script_id = _make_golden_script_id()
    trajectory = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#stale-selector", "rank": 0}], None, "dom_changed"),)
    )
    fake_llm = FakeLlmClient(responses=[{"action": "replace_selector", "selector": "#generate-report-btn"}])
    cache = ActionCacheStore()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(f"{fixture_portal_url}/dashboard.html")
        outcomes_1 = BrainLoop(
            page, llm=fake_llm, action_cache=cache, golden_script_id=golden_script_id
        ).run(trajectory)

        page.goto(f"{fixture_portal_url}/dashboard.html")  # fresh page, same DOM shape
        outcomes_2 = BrainLoop(
            page, llm=fake_llm, action_cache=cache, golden_script_id=golden_script_id
        ).run(trajectory)

        browser.close()

    assert outcomes_1[0].on_track is True
    assert outcomes_2[0].on_track is True
    assert len(fake_llm.calls) == 1  # the second run's repair came from the cache, not the LLM
