"""Runs the authoring pipeline locally, against the scratch environment
(atlas_engine.scratch_env) — the same live-browser-replay requirement
recording has applies to authoring too, since it replays the trajectory
against the real target portal — and submits the final result to the
deployed backend once the run reaches a terminal state. Used by
desktop/remote_app.py's local (non-proxied) authoring endpoints.
"""

import json
import threading
import uuid

import httpx

from atlas_engine.authoring.run import resume_authoring_run, start_authoring_run, stop_authoring_run
from atlas_engine.authoring_export import export_recording_for_authoring, import_recording_for_authoring
from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun, AuthoringRunEvent, CertificationRun, GoldenScript
from atlas_engine.llm.factory import create_llm_client
from atlas_engine.storage import get_store

_lock = threading.Lock()
# Maps a scratch-local authoring_run_id to the bookkeeping needed to
# submit its result once it finishes: which remote recording it's for,
# how to reach the deployed backend, and whether submission has already
# happened (so polling after completion doesn't double-submit).
_pending: dict[uuid.UUID, dict] = {}


def start_local_authoring(remote_base_url: str, cookies: dict[str, str], remote_recording_id: str) -> uuid.UUID:
    """Fetches the recording's data from the deployed backend, recreates
    it in the scratch environment, and starts the (unmodified) authoring
    pipeline against that scratch copy. Returns the scratch-local
    authoring_run_id to poll — unrelated to any id on the deployed
    backend; nothing there exists yet for this run until it's submitted.
    """
    export_response = httpx.get(
        f"{remote_base_url}/recordings/{remote_recording_id}/export-for-authoring", cookies=cookies, timeout=30.0,
    )
    export_response.raise_for_status()
    export_data = export_response.json()

    local_recording_id = import_recording_for_authoring(export_data)

    llm = create_llm_client(usage_context=(export_data["org"]["id"], remote_recording_id))
    authoring_run_id = start_authoring_run(local_recording_id, llm=llm)

    with _lock:
        _pending[authoring_run_id] = {
            "remote_recording_id": remote_recording_id,
            "remote_base_url": remote_base_url,
            "cookies": cookies,
            "submitted": False,
        }
    return authoring_run_id


def resume_local_authoring(authoring_run_id: uuid.UUID) -> None:
    resume_authoring_run(authoring_run_id)


def stop_local_authoring(authoring_run_id: uuid.UUID, *, timeout: float = 30.0) -> None:
    stop_authoring_run(authoring_run_id, timeout=timeout)


def submit_if_terminal(authoring_run_id: uuid.UUID) -> None:
    """Called after every local progress poll — a cheap no-op unless this
    run just reached a terminal state and hasn't been submitted yet.
    Submission happens here (poll-triggered) rather than as a callback
    from the authoring thread itself, so atlas_engine.authoring.run stays
    completely unmodified — it has no concept of "submit somewhere else
    when done" and doesn't need one."""
    with _lock:
        pending = _pending.get(authoring_run_id)
    if pending is None or pending["submitted"]:
        return

    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        if run is None or run.status not in ("completed", "failed", "cancelled"):
            return
        golden_script_id = run.golden_script_id
        repair_attempt = run.repair_attempt

        events = [
            {"phase": e.phase, "step_index": e.step_index, "level": e.level, "message": e.message}
            for e in session.query(AuthoringRunEvent)
            .filter_by(authoring_run_id=authoring_run_id).order_by(AuthoringRunEvent.seq).all()
        ]

        if golden_script_id is None:
            # Failed/cancelled before reaching the golden phase — nothing
            # meaningful to submit. Leave "submitted" unset so this stays
            # a no-op rather than silently discarding a real failure the
            # developer might want to retry.
            return

        golden = session.get(GoldenScript, golden_script_id)
        golden_status = golden.status
        script_text = get_store().read_text(golden.script_blob_ref)

        certification_runs = []
        evidence_blobs: dict[int, bytes] = {}
        for run_row in session.query(CertificationRun).filter_by(authoring_run_id=authoring_run_id).all():
            certification_runs.append({"run_number": run_row.run_number, "status": run_row.status})
            if run_row.failure_evidence_blob_ref:
                evidence_blobs[run_row.run_number] = get_store().read_bytes(run_row.failure_evidence_blob_ref)

    with _lock:
        pending["submitted"] = True  # set before the network call: a concurrent poll must not double-submit

    files = [("evidence_files", (f"evidence-{n}.png", data, "image/png")) for n, data in evidence_blobs.items()]
    httpx.post(
        f"{pending['remote_base_url']}/recordings/{pending['remote_recording_id']}/submit-authoring-result",
        data={
            "golden_status": golden_status,
            "script_text": script_text,
            "certification_runs": json.dumps(certification_runs),
            "events": json.dumps(events),
            "repair_attempt": str(repair_attempt),
        },
        files=files or None,
        cookies=pending["cookies"],
        timeout=60.0,
    )
