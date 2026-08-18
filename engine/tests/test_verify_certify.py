import json
import textwrap
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


def test_certify_golden_script_once_handles_subprocess_timeout(tmp_path, monkeypatch):
    """A certify subprocess that runs past _CERTIFY_TIMEOUT_SECONDS must be
    killed and reported as an ordinary (False, evidence) failure rather
    than left running or allowed to hang the caller. Patches the module's
    timeout constant to 1s (real default is 1200s) so this test completes
    quickly while still exercising the actual Popen/poll/kill path, not a
    mocked one -- certify_golden_script_once no longer calls
    subprocess.run at all (see the stop_event-interruptible rewrite), so
    mocking that function wouldn't exercise anything real anymore."""
    import atlas_engine.verify.certify as certify_module
    from atlas_engine.verify.certify import certify_golden_script_once

    monkeypatch.setattr(certify_module, "_CERTIFY_TIMEOUT_SECONDS", 1)

    script_path = tmp_path / "slow.py"
    script_path.write_text("import time\ntime.sleep(30)\n")

    passed, evidence, result = certify_golden_script_once(script_path)

    assert passed is False
    assert "timed out after 1s" in evidence
    assert result is None


def test_certify_golden_script_once_raises_cancelled_when_stop_event_fires(tmp_path):
    """A Stop click during certifying must interrupt an in-flight certify
    subprocess promptly, not wait for it (or all `runs` of them) to
    finish on their own -- a plain subprocess.run(timeout=1200) blocked
    the calling thread uninterruptibly for up to 20 minutes per run
    before this fix."""
    import threading
    import time

    import pytest

    from atlas_engine.verify.certify import CertifyCancelled, certify_golden_script_once

    script_path = tmp_path / "slow.py"
    script_path.write_text("import time\ntime.sleep(30)\n")

    stop_event = threading.Event()
    threading.Timer(0.7, stop_event.set).start()

    start = time.monotonic()
    with pytest.raises(CertifyCancelled):
        certify_golden_script_once(script_path, stop_event=stop_event)
    elapsed = time.monotonic() - start

    # Must return promptly once stop_event fires, not run the full 30s sleep.
    assert elapsed < 5


def test_certify_golden_script_once_does_not_raise_cancelled_if_stop_event_never_fires(tmp_path, fixture_portal_url):
    """stop_event is polled, not assumed set -- a normal, uninterrupted
    certification run must still succeed exactly as before."""
    from atlas_engine.verify.certify import certify_golden_script_once
    import threading

    script_path = _write_fixture_golden(tmp_path, fixture_portal_url)
    stop_event = threading.Event()

    passed, evidence, result = certify_golden_script_once(script_path, stop_event=stop_event)

    assert passed is True
    assert evidence is None


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
