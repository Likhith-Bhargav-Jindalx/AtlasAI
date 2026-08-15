from unittest.mock import MagicMock

from atlas_engine.brain.loop import run_step
from atlas_engine.brain.models import TrajectoryStep


def test_extract_step_runs_collect_page_data_and_attaches_it_to_the_outcome(monkeypatch):
    import atlas_engine.brain.loop as loop_module
    monkeypatch.setattr(
        loop_module, "collect_page_data", lambda page: {"fields": {"a": "1"}, "tables": []},
    )
    page = MagicMock()
    page.context.pages = [page]
    page.url = "https://example.com/"
    page.content.return_value = "<html></html>"

    step = TrajectoryStep(index=0, kind="extract", selector_candidates=[], value=None, expected_effect="none")
    outcome = run_step(page, step)

    assert outcome.on_track is True
    assert outcome.extracted_data == {"fields": {"a": "1"}, "tables": []}


def test_extract_step_never_resolves_a_locator_or_acts():
    """An extract step has no target element — run_step must not attempt
    candidate resolution or dispatch any click/fill against the page."""
    page = MagicMock()
    page.context.pages = [page]
    page.url = "https://example.com/"
    page.content.return_value = "<html></html>"

    step = TrajectoryStep(index=0, kind="extract", selector_candidates=[], value=None, expected_effect="none")
    run_step(page, step)

    page.locator.assert_not_called()
    page.click.assert_not_called()
