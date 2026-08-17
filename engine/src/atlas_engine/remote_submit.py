"""Submits a locally-captured recording to a remote deployed backend over
HTTPS instead of writing directly to a database and blob store — used by
the desktop exe when ATLAS_REMOTE_BACKEND_URL is set, so agent machines
never need direct database/storage credentials. The actual browser
automation still runs locally (it has to — it's the agent's own machine
and session); only what happens to the *finished* capture differs.
"""

import json
import logging
import uuid
from pathlib import Path

import httpx

from atlas_engine.capture.events import RecordedAction
from atlas_engine.recording_service import PageCapture

logger = logging.getLogger(__name__)


def submit_capture_to_remote(
    org_id: str,
    start_url: str,
    actions: list[RecordedAction],
    *,
    base_url: str,
    cookies: dict[str, str],
    request_id: uuid.UUID | None = None,
    role: str = "demo",
    user_id: str | None = None,
    video_path: Path | None = None,
    video_started_at_ms: int | None = None,
    video_ended_at_ms: int | None = None,
    additional_pages: list[PageCapture] | None = None,
    page_extractions: list[dict] | None = None,
    codegen_script: str | None = None,
    capture_artifacts: list[tuple[str, Path]] | None = None,
) -> uuid.UUID:
    """Same call signature as recording_service.persist_recording — this is
    passed as live_capture's persist_fn when running in remote-submit
    mode, so it's a drop-in replacement, not a special case in the capture
    code itself.

    org_id and user_id are accepted for signature compatibility but not
    sent: the remote /recordings/submit-capture endpoint derives both from
    the authenticated session (the cookies passed in), which is also more
    secure than trusting client-supplied identity. additional_pages
    (extra tabs/popups) isn't supported yet — the remote endpoint doesn't
    accept it, matching that endpoint's own documented limitation.
    """
    if additional_pages:
        logger.warning(
            "submit_capture_to_remote: %d additional page(s) captured but not supported by "
            "the remote submit-capture endpoint yet — only the main page is submitted",
            len(additional_pages),
        )

    data: dict[str, str] = {"start_url": start_url, "actions": json.dumps([vars(a) for a in actions]), "role": role}
    if request_id is not None:
        data["request_id"] = str(request_id)
    if video_started_at_ms is not None:
        data["video_started_at_ms"] = str(video_started_at_ms)
    if video_ended_at_ms is not None:
        data["video_ended_at_ms"] = str(video_ended_at_ms)
    if codegen_script is not None:
        data["codegen_script"] = codegen_script
    if page_extractions is not None:
        data["page_extractions"] = json.dumps(page_extractions)

    open_files = []
    try:
        files = {}
        if video_path is not None and video_path.exists():
            fh = open(video_path, "rb")
            open_files.append(fh)
            files["video"] = ("video.webm", fh, "video/webm")

        for name, path in capture_artifacts or []:
            if not path.exists():
                continue
            fh = open(path, "rb")
            open_files.append(fh)
            if name == "trace.zip":
                files["trace"] = (name, fh, "application/zip")
            elif name == "network.har":
                files["har"] = (name, fh, "application/json")

        response = httpx.post(
            f"{base_url}/recordings/submit-capture",
            data=data, files=files, cookies=cookies, timeout=120.0,
        )
        response.raise_for_status()
    finally:
        for fh in open_files:
            fh.close()

    return uuid.UUID(response.json()["recording_id"])
