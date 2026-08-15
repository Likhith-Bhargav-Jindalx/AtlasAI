import json
import uuid

from click.testing import CliRunner

from atlas_engine.cli import main
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, Organization
from atlas_engine.storage import get_store


def test_author_from_actions_command_produces_a_certified_golden_script(tmp_path, fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Test Org", slug=f"test-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    actions_path = tmp_path / "actions.json"
    actions_path.write_text(
        json.dumps(
            [
                {"index": 0, "kind": "fill", "selector_candidates": [{"selector": "#username", "rank": 0}], "value": "demo", "url_before": f"{fixture_portal_url}/login.html", "url_after": f"{fixture_portal_url}/login.html", "dom_before": "", "dom_after": ""},
                {"index": 1, "kind": "fill", "selector_candidates": [{"selector": "#password", "rank": 0}], "value": "demo-pass", "url_before": f"{fixture_portal_url}/login.html", "url_after": f"{fixture_portal_url}/login.html", "dom_before": "", "dom_after": ""},
                {"index": 2, "kind": "click", "selector_candidates": [{"selector": "#login-btn", "rank": 0}], "value": None, "url_before": f"{fixture_portal_url}/login.html", "url_after": f"{fixture_portal_url}/dashboard.html", "dom_before": "", "dom_after": ""},
                {"index": 3, "kind": "click", "selector_candidates": [{"selector": "#generate-report-btn", "rank": 0}], "value": None, "url_before": f"{fixture_portal_url}/dashboard.html", "url_after": f"{fixture_portal_url}/dashboard.html", "dom_before": "x", "dom_after": "y"},
            ]
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "author-from-actions",
            "--org-id", org_id,
            "--start-url", f"{fixture_portal_url}/login.html",
            "--actions-file", str(actions_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "CERTIFIED" in result.output

    with session_scope() as session:
        scripts = session.query(GoldenScript).filter_by(org_id=uuid.UUID(org_id)).all()
        assert len(scripts) == 1
        assert scripts[0].status == "certified"
        script_text = get_store().read_text(scripts[0].script_blob_ref)
        assert "atlas_engine" not in script_text
