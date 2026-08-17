"""activate_scratch_environment mutates atlas_engine.db/config's
process-global state with no restore — by design, since it's meant to be
called once, early, in the desktop exe's own process. That means it must
never run inside the main pytest process: doing so would silently repoint
every other test's database connection at a throwaway SQLite file for the
rest of the session. Every test here runs the real thing in a subprocess
instead, using this same venv's own interpreter (atlas_engine is
editable-installed there, so no sys.path setup is needed).
"""

import subprocess
import sys


def _run(script: str) -> str:
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return result.stdout


def test_activate_scratch_environment_creates_a_working_isolated_database():
    output = _run(
        "from atlas_engine.scratch_env import activate_scratch_environment\n"
        "from atlas_engine.db import session_scope\n"
        "from atlas_engine.db.models import Organization\n"
        "activate_scratch_environment()\n"
        "with session_scope() as session:\n"
        "    org = Organization(name='Scratch Org', slug='scratch-org')\n"
        "    session.add(org)\n"
        "    session.flush()\n"
        "    print(org.id)\n"
    )
    assert output.strip()  # a real UUID was printed — the insert succeeded


def test_activate_scratch_environment_does_not_reuse_the_default_database_url():
    output = _run(
        "from atlas_engine.config import settings\n"
        "before = settings.database_url\n"
        "from atlas_engine.scratch_env import activate_scratch_environment\n"
        "activate_scratch_environment()\n"
        "print(before != settings.database_url and settings.database_url.startswith('sqlite:///'))\n"
    )
    assert output.strip() == "True"


def test_authoring_run_events_can_be_inserted_against_the_scratch_database():
    """This is the actual regression test for the seq-assignment fix:
    AuthoringRunEvent.seq is a Postgres IDENTITY column with no SQLite
    equivalent on a non-primary-key column — without
    scratch_env's before_insert listener, this insert fails with
    `NOT NULL constraint failed: authoring_run_events.seq`."""
    output = _run(
        "import uuid\n"
        "from atlas_engine.scratch_env import activate_scratch_environment\n"
        "from atlas_engine.authoring.events import log_event\n"
        "from atlas_engine.db import session_scope\n"
        "from atlas_engine.db.models import AuthoringRun, Organization, PortalProfile, Recording, ReferenceTrajectory\n"
        "activate_scratch_environment()\n"
        "with session_scope() as session:\n"
        "    org = Organization(name='o', slug='o'); session.add(org); session.flush()\n"
        "    profile = PortalProfile(org_id=org.id, domain='example.com'); session.add(profile); session.flush()\n"
        "    recording = Recording(org_id=org.id, portal_profile_id=profile.id, capture_blob_prefix='x'); session.add(recording); session.flush()\n"
        "    session.add(ReferenceTrajectory(recording_id=recording.id, trajectory_blob_ref='y'))\n"
        "    run = AuthoringRun(org_id=org.id, recording_id=recording.id); session.add(run); session.flush()\n"
        "    run_id = run.id\n"
        "log_event(run_id, phase='replay', message='first')\n"
        "log_event(run_id, phase='replay', message='second')\n"
        "with session_scope() as session:\n"
        "    from atlas_engine.db.models import AuthoringRunEvent\n"
        "    seqs = [e.seq for e in session.query(AuthoringRunEvent).filter_by(authoring_run_id=run_id).order_by(AuthoringRunEvent.seq).all()]\n"
        "    print(seqs)\n"
    )
    assert output.strip() == "[1, 2]"
