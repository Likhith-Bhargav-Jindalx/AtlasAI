import json
import uuid
from pathlib import Path

import click
from playwright.sync_api import sync_playwright

from atlas_engine.capture.events import RecordedAction
from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.codegen.emit import emit_golden_script
from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, PortalProfile, Recording
from atlas_engine.portal_profile.infer import infer_portal_profile
from atlas_engine.storage import LocalRunStore
from atlas_engine.trajectory.extract import extract_trajectory
from atlas_engine.verify.certify import certify_golden_script


@click.group()
def main() -> None:
    """Atlas AI engine CLI."""


@main.command("record")
@click.argument("url")
@click.option("--org-id", required=True)
def record(url: str, org_id: str) -> None:
    """Open a headed browser at URL, capture actions until you press Enter,
    persist the recording, and print its ID."""
    store = LocalRunStore(root=settings.ensure_run_root())
    run_dir = store.new_run_dir(prefix="recordings")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(url)

        with CaptureSession(page) as capture:
            click.echo("Recording... perform your steps in the opened browser, then press Enter here to finish.")
            input()

        browser.close()

    run_dir.write_json("actions.json", [vars(a) for a in capture.actions])

    with session_scope() as session:
        profile_data = infer_portal_profile(url, capture.actions)
        profile = PortalProfile(
            org_id=uuid.UUID(org_id),
            domain=profile_data.domain,
            inferred_login_url=profile_data.login_url,
            inferred_success_signal=profile_data.success_signal,
            confidence_score=profile_data.confidence,
        )
        session.add(profile)
        session.flush()

        recording = Recording(
            org_id=uuid.UUID(org_id),
            portal_profile_id=profile.id,
            status="captured",
            capture_blob_prefix=run_dir.blob_ref,
        )
        session.add(recording)
        session.flush()
        click.echo(f"Recording saved: {recording.id}")
        click.echo(f"Actions file: {run_dir.path / 'actions.json'}")


def author_from_recorded_actions(org_id: str, start_url: str, actions: list[RecordedAction]) -> dict:
    """Shared authoring logic used by both the CLI and the local API.
    Deterministic: capture -> trajectory -> emit -> certify. No brain/LLM
    involvement on this path since there's no live browser session here to
    repair against — that happens in `atlas author` (not yet built), which
    replays the trajectory live via BrainLoop before emitting.

    Returns {"status", "script_path", "golden_script_id"}.
    """
    store = LocalRunStore(root=settings.ensure_run_root())
    run_dir = store.new_run_dir(prefix="goldens")

    trajectory = extract_trajectory(actions)
    script_text = emit_golden_script(trajectory, start_url=start_url)
    script_path = run_dir.path / "golden.py"
    script_path.write_text(script_text)

    certification = certify_golden_script(script_path, runs=2)
    status = "certified" if certification.is_certified else "draft"

    with session_scope() as session:
        profile = PortalProfile(org_id=uuid.UUID(org_id), domain=start_url.split("/")[2])
        session.add(profile)
        session.flush()

        golden = GoldenScript(
            org_id=uuid.UUID(org_id),
            portal_profile_id=profile.id,
            version=1,
            script_blob_ref=str(script_path),
            status=status,
        )
        session.add(golden)
        session.flush()
        golden_script_id = str(golden.id)

    return {"status": status, "script_path": str(script_path), "golden_script_id": golden_script_id}


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


if __name__ == "__main__":
    main()
