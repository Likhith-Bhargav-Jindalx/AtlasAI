from pathlib import Path

from atlas_engine.codegen.emit import emit_golden_script
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.verify.certify import certify_golden_script


def _write_fixture_golden(tmp_path: Path, fixture_portal_url: str) -> Path:
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )
    script = emit_golden_script(trajectory, start_url=f"{fixture_portal_url}/login.html")
    path = tmp_path / "golden.py"
    path.write_text(script)
    return path


def test_certify_golden_script_succeeds_when_every_run_succeeds(tmp_path, fixture_portal_url):
    script_path = _write_fixture_golden(tmp_path, fixture_portal_url)

    result = certify_golden_script(script_path, runs=2)

    assert result.runs == 2
    assert result.succeeded == 2
    assert result.is_certified is True


def test_certify_golden_script_fails_when_a_run_fails(tmp_path):
    script_path = tmp_path / "broken.py"
    script_path.write_text("import sys\nsys.exit(1)\n")

    result = certify_golden_script(script_path, runs=2)

    assert result.succeeded == 0
    assert result.is_certified is False


def test_certify_golden_script_once_returns_true_and_no_evidence_on_success(tmp_path, fixture_portal_url):
    from atlas_engine.verify.certify import certify_golden_script_once

    script_path = _write_fixture_golden(tmp_path, fixture_portal_url)

    passed, evidence = certify_golden_script_once(script_path)

    assert passed is True
    assert evidence is None


def test_certify_golden_script_once_returns_false_and_evidence_on_failure(tmp_path):
    from atlas_engine.verify.certify import certify_golden_script_once

    script_path = tmp_path / "broken.py"
    script_path.write_text("import sys\nprint('boom', file=sys.stderr)\nsys.exit(1)\n")

    passed, evidence = certify_golden_script_once(script_path)

    assert passed is False
    assert "boom" in evidence
