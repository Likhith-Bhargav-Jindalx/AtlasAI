import json
import uuid
from pathlib import Path

from atlas_engine.capture.events import RecordedAction
from atlas_engine.cli import author_from_recorded_actions
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, Organization, Recording, ReferenceTrajectory


def test_author_from_recorded_actions_persists_recording_and_trajectory(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Persist Test Org", slug=f"persist-test-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    actions = [
        RecordedAction(0, "fill", [{"selector": "#username", "rank": 0}], "demo", f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/login.html", "", ""),
        RecordedAction(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/login.html", "", ""),
        RecordedAction(2, "click", [{"selector": "#login-btn", "rank": 0}], None, f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/dashboard.html", "", ""),
        RecordedAction(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, f"{fixture_portal_url}/dashboard.html", f"{fixture_portal_url}/dashboard.html", "x", "y"),
    ]

    result = author_from_recorded_actions(org_id, f"{fixture_portal_url}/login.html", actions)
    assert result["status"] == "certified"

    with session_scope() as session:
        golden = session.get(GoldenScript, uuid.UUID(result["golden_script_id"]))
        assert golden.created_from_recording_id is not None

        recording = session.get(Recording, golden.created_from_recording_id)
        assert recording.status == "captured"

        trajectory_row = (
            session.query(ReferenceTrajectory).filter_by(recording_id=recording.id).one()
        )
        assert trajectory_row.step_count == 4

        data = json.loads(Path(trajectory_row.trajectory_blob_ref).read_text())
        assert len(data["steps"]) == 4
        assert data["steps"][3]["selector_candidates"][0]["selector"] == "#generate-report-btn"
