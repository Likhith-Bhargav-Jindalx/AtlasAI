import json
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

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
    # The login block's username/password are baked in as --username/
    # --password argparse defaults (the recorded "demo"/"demo-pass" fill
    # values), exactly like any other CLI param — no env var needed for the
    # certify subprocess to log in.
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

    passed, evidence, result = certify_golden_script_once(script_path)

    assert passed is True
    assert evidence is None


def test_certify_golden_script_once_returns_false_and_evidence_on_failure(tmp_path):
    from atlas_engine.verify.certify import certify_golden_script_once

    script_path = tmp_path / "broken.py"
    script_path.write_text("import sys\nprint('boom', file=sys.stderr)\nsys.exit(1)\n")

    passed, evidence, result = certify_golden_script_once(script_path)

    assert passed is False
    assert "boom" in evidence
    assert result is None


def test_certify_golden_script_once_handles_subprocess_timeout(tmp_path):
    """subprocess.run(..., timeout=60) raises TimeoutExpired on expiry rather
    than returning — certify_golden_script_once must catch that and report it
    as an ordinary (False, evidence) failure rather than letting it propagate
    and hard-fail the whole authoring run. Mocks subprocess.run instead of
    actually sleeping 60+ seconds in the test suite."""
    from atlas_engine.verify.certify import certify_golden_script_once

    script_path = tmp_path / "slow.py"
    script_path.write_text("import time\ntime.sleep(120)\n")

    timeout_exc = subprocess.TimeoutExpired(cmd=["python", str(script_path)], timeout=60, output="partial-out", stderr="partial-err")
    with patch("atlas_engine.verify.certify.subprocess.run", side_effect=timeout_exc):
        passed, evidence, result = certify_golden_script_once(script_path)

    assert passed is False
    assert "timed out" in evidence
    assert "partial-out" in evidence
    assert "partial-err" in evidence
    assert result is None


def test_certify_golden_script_once_parses_stdout_json_on_success(tmp_path):
    from atlas_engine.verify.certify import certify_golden_script_once

    script_path = tmp_path / "golden.py"
    script_path.write_text(textwrap.dedent('''
        import json
        print(json.dumps({"fileCount": 2, "filesTransferred": ["a.csv", "b.csv"]}))
    '''))

    ok, failure_output, result = certify_golden_script_once(script_path)

    assert ok is True
    assert failure_output is None
    assert result == {"fileCount": 2, "filesTransferred": ["a.csv", "b.csv"]}


def test_certify_golden_script_once_tolerates_unparseable_stdout(tmp_path):
    from atlas_engine.verify.certify import certify_golden_script_once

    script_path = tmp_path / "golden.py"
    script_path.write_text("print('not json')\n")

    ok, failure_output, result = certify_golden_script_once(script_path)

    assert ok is True
    assert result is None


def test_certify_golden_script_once_returns_no_result_on_failure(tmp_path):
    from atlas_engine.verify.certify import certify_golden_script_once

    script_path = tmp_path / "golden.py"
    script_path.write_text("import sys; sys.exit(1)\n")

    ok, failure_output, result = certify_golden_script_once(script_path)

    assert ok is False
    assert result is None


def test_certify_once_passes_an_isolated_diagnostics_directory_to_a_golden(tmp_path):
    """Certification must invoke the emitted script as a genuine standalone
    run while giving that process its own failure-evidence directory."""
    from atlas_engine.verify.certify import certify_golden_script_once

    script_path = tmp_path / "golden.py"
    script_path.write_text(textwrap.dedent('''
        import argparse
        import json
        from pathlib import Path

        parser = argparse.ArgumentParser()
        parser.add_argument("--diagnostics-dir")
        args = parser.parse_args()
        Path(args.diagnostics_dir, "received.txt").write_text("yes")
        print(json.dumps({"diagnostics_dir": args.diagnostics_dir}))
    '''))
    diagnostics_dir = tmp_path / "certify-1"

    ok, failure_output, result = certify_golden_script_once(
        script_path, diagnostics_dir=diagnostics_dir,
    )

    assert ok is True
    assert failure_output is None
    assert result == {"diagnostics_dir": str(diagnostics_dir)}
    assert (diagnostics_dir / "received.txt").read_text() == "yes"
