import json
import shutil
import tempfile
import uuid
from pathlib import Path

import click
from playwright.sync_api import sync_playwright

from atlas_engine.auth.passwords import hash_password
from atlas_engine.authoring_service import author_from_recording
from atlas_engine.capture.events import RecordedAction
from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.capture.video import finalize_video, start_capture_context
from atlas_engine.db import session_scope
from atlas_engine.db.models import User
from atlas_engine.llm.client import LlmClient
from atlas_engine.llm.factory import create_llm_client
from atlas_engine.recording_service import (
    claim_recording_request,
    create_recording_request,
    list_recording_requests,
    persist_recording,
)
from atlas_engine.verify.heal import approve_heal_event, heal_golden_script, reject_heal_event


@click.group()
def main() -> None:
    """Atlas AI engine CLI."""


@main.command("record")
@click.argument("url")
@click.option("--org-id", required=True)
@click.option("--request-id", default=None)
@click.option("--role", default="demo", type=click.Choice(["demo", "authoring"]))
def record(url: str, org_id: str, request_id: str | None, role: str) -> None:
    """Open a headed browser at URL, capture actions until you press Enter,
    persist the recording (with video), and print its ID."""
    codegen_dir = None
    codegen_script = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context, page, video_started_at_ms, video_dir = start_capture_context(browser)
            codegen_dir = Path(tempfile.mkdtemp(prefix="atlas-codegen-"))
            codegen_path = codegen_dir / "codegen.py"
            from atlas_engine.capture.codegen import attach_codegen_recorder, strip_extraction_markers
            attach_codegen_recorder(context, codegen_path)
            page.goto(url)

            with CaptureSession(page) as capture:
                click.echo("Recording... perform your steps in the opened browser, then press Enter here to finish.")
                input()
                try:
                    page.title()
                except Exception:
                    capture.mark_browser_disconnected()

            video_path, video_ended_at_ms = finalize_video(context, page)
            try:
                browser.close()
            except Exception:
                pass

        try:
            if codegen_path.exists() and codegen_path.stat().st_size > 0:
                codegen_script = strip_extraction_markers(codegen_path.read_text())
        except Exception:
            pass

        recording_id = persist_recording(
            org_id, url, capture.actions,
            request_id=uuid.UUID(request_id) if request_id else None,
            role=role,
            video_path=video_path,
            video_started_at_ms=video_started_at_ms,
            video_ended_at_ms=video_ended_at_ms,
            codegen_script=codegen_script,
        )
    finally:
        if video_dir is not None:
            shutil.rmtree(video_dir, ignore_errors=True)
        if codegen_dir is not None:
            shutil.rmtree(codegen_dir, ignore_errors=True)
    click.echo(f"Recording saved: {recording_id}")


def author_from_recorded_actions(
    org_id: str, start_url: str, actions: list[RecordedAction], *, llm: LlmClient | None = None
) -> dict:
    """Backward-compatible convenience wrapper: persist the recording, then
    author from it. Kept for existing callers (the author-from-actions CLI
    command and the /author API endpoint); new code should call
    recording_service.persist_recording and authoring_service.
    author_from_recording separately when the two need to happen at
    different times or by different people.
    """
    recording_id = persist_recording(org_id, start_url, actions)
    return author_from_recording(recording_id, llm=llm)


@main.command("author-from-actions")
@click.option("--org-id", required=True)
@click.option("--start-url", required=True)
@click.option("--actions-file", required=True, type=click.Path(exists=True))
def author_from_actions(org_id: str, start_url: str, actions_file: str) -> None:
    """Author a golden script from a previously captured actions.json file
    (the output of `atlas record`)."""
    raw = json.loads(Path(actions_file).read_text())
    actions = [RecordedAction(**item) for item in raw]
    result = author_from_recorded_actions(org_id, start_url, actions)
    if result["status"] == "certified":
        click.echo(f"CERTIFIED golden script: {result['script_path']}")
    else:
        click.echo(f"NOT certified: {result['script_path']}")


@main.command("heal")
@click.argument("golden_script_id")
def heal(golden_script_id: str) -> None:
    """Re-validate a golden script against its live portal and, if a step
    has drifted, propose a repaired version pending approval."""
    llm = create_llm_client()
    result = heal_golden_script(uuid.UUID(golden_script_id), llm=llm)
    if result["status"] == "already_certified":
        click.echo("Already on-track — nothing to heal.")
    else:
        click.echo(f"Heal event {result['heal_event_id']} created — new version status: {result['new_status']}")
        click.echo(f"Diff: {json.dumps(result['diff'], indent=2)}")


@main.command("approve-heal")
@click.argument("heal_event_id")
def approve_heal(heal_event_id: str) -> None:
    """Approve a pending heal event: activate its proposed version and
    deprecate the one it replaces."""
    result = approve_heal_event(uuid.UUID(heal_event_id))
    click.echo(f"Approved: v{result['deprecated_version']} -> v{result['active_version']} is now active")


@main.command("reject-heal")
@click.argument("heal_event_id")
def reject_heal(heal_event_id: str) -> None:
    """Reject a pending heal event: the proposed version is deprecated,
    the currently active version is left untouched."""
    reject_heal_event(uuid.UUID(heal_event_id))
    click.echo("Rejected — proposed version deprecated, active version unchanged")


@main.command("request-recording")
@click.option("--org-id", required=True)
@click.option("--requested-by", required=True)
@click.option("--target-url", required=True)
@click.option("--title", required=True)
@click.option("--description", default=None)
def request_recording(org_id: str, requested_by: str, target_url: str, title: str, description: str | None) -> None:
    """Operations agent: submit a request describing what needs
    automating. Follow up with `atlas record` against the same
    --request-id to attach the demo recording."""
    request_id = create_recording_request(org_id, requested_by, target_url, title, description)
    click.echo(f"Recording request created: {request_id}")


@main.command("list-requests")
@click.option("--org-id", required=True)
@click.option("--status", default=None, type=click.Choice(["open", "submitted", "fulfilled", "cancelled"]))
def list_requests(org_id: str, status: str | None) -> None:
    """Developer: list recording requests, optionally filtered by status."""
    requests = list_recording_requests(org_id, status=status)
    if not requests:
        click.echo("No recording requests found.")
        return
    for request in requests:
        claimed = f" (claimed by {request['claimed_by']})" if request["claimed_by"] else ""
        click.echo(f"{request['id']}  [{request['status']}]  {request['title']} — {request['target_url']}{claimed}")


@main.command("claim-request")
@click.argument("request_id")
@click.option("--developer-id", required=True)
def claim_request(request_id: str, developer_id: str) -> None:
    """Developer: mark a request as being worked on (advisory only)."""
    claim_recording_request(uuid.UUID(request_id), uuid.UUID(developer_id))
    click.echo(f"Claimed request {request_id}")


@main.command("author")
@click.argument("recording_id")
def author(recording_id: str) -> None:
    """Author a golden script from an already-persisted recording —
    the decoupled counterpart to author-from-actions, for recordings that
    already exist as Recording rows (via `atlas record`, by anyone)."""
    llm = create_llm_client()
    result = author_from_recording(uuid.UUID(recording_id), llm=llm)
    if result["status"] == "certified":
        click.echo(f"CERTIFIED golden script: {result['script_path']}")
    else:
        click.echo(f"NOT certified: {result['script_path']}")


@main.command("create-user")
@click.option("--org-id", required=True)
@click.option("--email", required=True)
@click.option("--password", required=True)
@click.option("--role", default="member", type=click.Choice(["owner", "admin", "member"]))
@click.option("--job-function", default=None, type=click.Choice(["operations_agent", "developer"]))
def create_user(org_id: str, email: str, password: str, role: str, job_function: str | None) -> None:
    """Dev-only user creation — no signup flow exists yet."""
    with session_scope() as session:
        user = User(
            org_id=uuid.UUID(org_id),
            email=email,
            password_hash=hash_password(password),
            role=role,
            job_function=job_function,
        )
        session.add(user)
        session.flush()
        click.echo(f"User created: {user.id}")


if __name__ == "__main__":
    main()
