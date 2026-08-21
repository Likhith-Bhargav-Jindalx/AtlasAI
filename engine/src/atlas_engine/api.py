import json
import logging
import tempfile
import uuid
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from atlas_engine.observability import RequestIdMiddleware, configure_logging, get_request_id

logger = logging.getLogger(__name__)

from atlas_engine.auth.deps import CurrentUser, get_current_user, require_job_function
from atlas_engine.auth.passwords import verify_password
from atlas_engine.auth.session import create_session_token, verify_session_token
from atlas_engine.authoring.run import resume_authoring_run, start_authoring_run, stop_authoring_run
from atlas_engine.authoring_export import export_recording_for_authoring
from atlas_engine.capture.events import RecordedAction
from atlas_engine.cli import author_from_recorded_actions
from atlas_engine.db import session_scope
from atlas_engine.db.models import (
    AuthoringRun,
    AuthoringRunEvent,
    CertificationRun,
    GoldenScript,
    HealEvent,
    Recording,
    RecordingRequest,
    User,
    VideoAsset,
)
from atlas_engine.db.repo import next_golden_script_version
from atlas_engine.live_capture import start_live_capture, stop_live_capture
from atlas_engine.llm.factory import create_llm_client
from atlas_engine.llm.usage import list_llm_usage_summary
from atlas_engine.recording_form_validation import (
    validate_date_param_logic,
    validate_payers_required,
    validate_post_processing,
    validate_sub_reason_required,
)
from atlas_engine.recording_service import (
    claim_recording_request,
    create_recording_request,
    get_recording_detail,
    list_carc_codes,
    list_clients,
    list_page_extractions,
    list_payers,
    list_recording_requests,
    list_recordings,
    list_systems,
    persist_recording,
    submit_recording_for_authoring,
    update_recording_details,
)
from atlas_engine.storage import get_store
from atlas_engine.verify.heal import approve_heal_event, heal_golden_script, list_heal_events, reject_heal_event

app = FastAPI(title="Atlas Engine Local API")

configure_logging()
app.add_middleware(RequestIdMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5183"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    from fastapi.responses import JSONResponse

    request_id = get_request_id()
    if not request_id:
        # Fallback to same logic as middleware if context var not available (e.g., sync func in thread)
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())

    logger.exception("Unhandled exception handling %s %s", request.method, request.url.path)
    response = JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "request_id": request_id},
    )
    response.headers["X-Request-Id"] = request_id
    return response


SESSION_COOKIE_NAME = "atlas_session"


class AuthorRequest(BaseModel):
    org_id: str
    start_url: str
    actions: list[dict]


class AuthorResponse(BaseModel):
    status: str
    script_path: str
    golden_script_id: str


class HealResponse(BaseModel):
    status: str
    heal_event_id: str | None = None
    new_golden_script_id: str | None = None
    new_status: str | None = None
    diff: list[dict] | None = None
    golden_script_id: str | None = None


class ApprovalResponse(BaseModel):
    heal_event_id: str
    status: str | None = None
    deprecated_version: int | None = None
    active_version: int | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/author", response_model=AuthorResponse)
def author(request: AuthorRequest) -> AuthorResponse:
    actions = [RecordedAction(**item) for item in request.actions]
    result = author_from_recorded_actions(request.org_id, request.start_url, actions)
    return AuthorResponse(**result)


@app.post("/golden-scripts/{golden_script_id}/heal", response_model=HealResponse)
def heal_endpoint(golden_script_id: str) -> HealResponse:
    with session_scope() as session:
        golden = session.get(GoldenScript, uuid.UUID(golden_script_id))
        usage_context = (str(golden.org_id), str(golden.created_from_recording_id)) if golden else None
    llm = create_llm_client(usage_context=usage_context)
    result = heal_golden_script(uuid.UUID(golden_script_id), llm=llm)
    return HealResponse(**result)


@app.post("/heal-events/{heal_event_id}/approve", response_model=ApprovalResponse)
def approve_endpoint(
    heal_event_id: str, _user: CurrentUser = Depends(require_job_function("developer")),
) -> ApprovalResponse:
    result = approve_heal_event(uuid.UUID(heal_event_id))
    return ApprovalResponse(**result)


@app.post("/heal-events/{heal_event_id}/reject", response_model=ApprovalResponse)
def reject_endpoint(
    heal_event_id: str, _user: CurrentUser = Depends(require_job_function("developer")),
) -> ApprovalResponse:
    result = reject_heal_event(uuid.UUID(heal_event_id))
    return ApprovalResponse(**result)


@app.get("/heal-events")
def list_heal_events_endpoint(org_id: str, status: str | None = None) -> list[dict]:
    return list_heal_events(org_id, status=status)


@app.get("/heal-events/{heal_event_id}/screenshots/{step_index}/{when}")
def get_heal_event_screenshot_endpoint(heal_event_id: str, step_index: int, when: str):
    if when not in ("before", "after"):
        raise HTTPException(status_code=404, detail="'when' must be 'before' or 'after'")

    with session_scope() as session:
        heal_event = session.get(HealEvent, uuid.UUID(heal_event_id))
        if heal_event is None:
            raise HTTPException(status_code=404, detail=f"No such heal event: {heal_event_id}")
        diff_summary_blob_ref = heal_event.diff_summary_blob_ref

    diff_summary = get_store().read_json(diff_summary_blob_ref) if diff_summary_blob_ref else {}
    entry = next(
        (c for c in diff_summary.get("changes", []) if c["step_index"] == step_index), None,
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="No diff entry for this step index")
    screenshot_path = entry.get(f"{when}_screenshot")

    if not screenshot_path or not Path(screenshot_path).exists():
        raise HTTPException(status_code=404, detail="Screenshot not available")
    return FileResponse(screenshot_path, media_type="image/png")


class CreateRecordingRequestBody(BaseModel):
    org_id: str
    requested_by: str
    target_url: str
    title: str
    description: str | None = None
    # Recording form Section A
    lob: str | None = None
    category: str | None = None
    action: str | None = None
    system_id: str | None = None
    client_scope: list[str] | None = None
    reuse_scope: str | None = None
    # Recording form v2
    ehr: str | None = None
    specific_to: str | None = None


class RecordingRequestCreatedResponse(BaseModel):
    request_id: str


class ClaimRequestBody(BaseModel):
    developer_id: str


@app.post("/recording-requests", response_model=RecordingRequestCreatedResponse)
def create_recording_request_endpoint(
    body: CreateRecordingRequestBody, _user: CurrentUser = Depends(get_current_user),
) -> RecordingRequestCreatedResponse:
    # client_scope (and the rest of Section A) is required by the frontend
    # form, not enforced here — this endpoint is also the one path CLI
    # callers and pre-existing simple title+URL flows still use, and they
    # never supply these fields at all.
    # requested_by is always the authenticated caller, never trusted from
    # the client — otherwise anyone could attribute a request to someone
    # else's inbox.
    request_id = create_recording_request(
        str(_user.org_id), str(_user.id), body.target_url, body.title, body.description,
        lob=body.lob, category=body.category, action=body.action, system_id=body.system_id,
        client_scope=body.client_scope, reuse_scope=body.reuse_scope,
        ehr=body.ehr, specific_to=body.specific_to,
    )
    return RecordingRequestCreatedResponse(request_id=str(request_id))


@app.get("/recording-requests")
def list_recording_requests_endpoint(
    status: str | None = None, lob: str | None = None, system_id: str | None = None,
    client_id: str | None = None, _user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    # Same pattern as GET /recordings: org_id always comes from the
    # session, and an operations agent's own id is forced as the
    # requested_by filter so their inbox only ever shows their own tasks.
    requested_by = None
    if _user.job_function == "operations_agent" and _user.role not in ("owner", "admin"):
        requested_by = str(_user.id)
    return list_recording_requests(
        str(_user.org_id), status=status, requested_by=requested_by,
        lob=lob, system_id=system_id, client_id=client_id,
    )


@app.get("/master-data/systems")
def list_systems_endpoint(_user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    return list_systems(str(_user.org_id))


@app.get("/master-data/payers")
def list_payers_endpoint(_user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    return list_payers(str(_user.org_id))


@app.get("/master-data/clients")
def list_clients_endpoint(_user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    return list_clients(str(_user.org_id))


@app.get("/master-data/carc-codes")
def list_carc_codes_endpoint(_user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    return list_carc_codes(str(_user.org_id))


@app.post("/recording-requests/{request_id}/claim")
def claim_recording_request_endpoint(
    request_id: str, body: ClaimRequestBody, _user: CurrentUser = Depends(require_job_function("developer")),
) -> dict:
    claim_recording_request(uuid.UUID(request_id), uuid.UUID(body.developer_id))
    return {"ok": True}


class StartAuthoringRunResponse(BaseModel):
    authoring_run_id: str


@app.post("/recordings/{recording_id}/author", response_model=StartAuthoringRunResponse)
def author_from_recording_endpoint(
    recording_id: str, _user: CurrentUser = Depends(require_job_function("developer")),
) -> StartAuthoringRunResponse:
    with session_scope() as session:
        recording = session.get(Recording, uuid.UUID(recording_id))
        usage_context = (str(recording.org_id), recording_id) if recording else None
    llm = create_llm_client(usage_context=usage_context)
    try:
        authoring_run_id = start_authoring_run(uuid.UUID(recording_id), llm=llm)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return StartAuthoringRunResponse(authoring_run_id=str(authoring_run_id))


@app.get("/llm-usage/summary")
def llm_usage_summary_endpoint(
    org_id: str, _user: CurrentUser = Depends(require_job_function("developer")),
) -> dict:
    return list_llm_usage_summary(org_id)


class AuthoringRunEventResponse(BaseModel):
    phase: str
    step_index: int | None = None
    level: str
    message: str


class CertificationRunResponse(BaseModel):
    run_number: int
    status: str
    failure_evidence_blob_ref: str | None = None


class GoldenScriptSummaryResponse(BaseModel):
    id: str
    version: int
    status: str
    script_text: str | None = None


class AuthoringRunDetailResponse(BaseModel):
    id: str
    status: str
    current_phase: str
    repair_attempt: int
    events: list[AuthoringRunEventResponse]
    golden_script: GoldenScriptSummaryResponse | None = None
    certification_runs: list[CertificationRunResponse]


@app.get("/authoring-runs/{authoring_run_id}", response_model=AuthoringRunDetailResponse)
def get_authoring_run_endpoint(authoring_run_id: str) -> AuthoringRunDetailResponse:
    try:
        run_uuid = uuid.UUID(authoring_run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"No such authoring run: {authoring_run_id}")

    with session_scope() as session:
        run = session.get(AuthoringRun, run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail=f"No such authoring run: {authoring_run_id}")

        events = (
            session.query(AuthoringRunEvent)
            .filter_by(authoring_run_id=run.id)
            .order_by(AuthoringRunEvent.seq)
            .all()
        )
        certification_runs = (
            session.query(CertificationRun)
            .filter_by(authoring_run_id=run.id)
            .order_by(CertificationRun.run_number)
            .all()
        )

        golden_script = None
        if run.golden_script_id is not None:
            golden = session.get(GoldenScript, run.golden_script_id)
            if golden is not None:
                script_text = None
                store = get_store()
                if store.exists(golden.script_blob_ref):
                    script_text = store.read_text(golden.script_blob_ref)
                golden_script = GoldenScriptSummaryResponse(
                    id=str(golden.id), version=golden.version, status=golden.status, script_text=script_text,
                )

        return AuthoringRunDetailResponse(
            id=str(run.id),
            status=run.status,
            current_phase=run.current_phase,
            repair_attempt=run.repair_attempt,
            events=[
                AuthoringRunEventResponse(
                    phase=e.phase, step_index=e.step_index, level=e.level, message=e.message,
                )
                for e in events
            ],
            golden_script=golden_script,
            certification_runs=[
                CertificationRunResponse(
                    run_number=c.run_number, status=c.status, failure_evidence_blob_ref=c.failure_evidence_blob_ref,
                )
                for c in certification_runs
            ],
        )


@app.post("/authoring-runs/{authoring_run_id}/resume")
def resume_authoring_run_endpoint(
    authoring_run_id: str, _user: CurrentUser = Depends(require_job_function("developer")),
) -> dict:
    with session_scope() as session:
        run = session.get(AuthoringRun, uuid.UUID(authoring_run_id))
        if run is None:
            raise HTTPException(status_code=404, detail=f"No such authoring run: {authoring_run_id}")
        if run.status != "waiting_for_developer":
            raise HTTPException(
                status_code=409, detail=f"Authoring run is not waiting for a developer (status={run.status})"
            )
    try:
        resume_authoring_run(uuid.UUID(authoring_run_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True}


@app.post("/authoring-runs/{authoring_run_id}/stop")
def stop_authoring_run_endpoint(
    authoring_run_id: str, _user: CurrentUser = Depends(require_job_function("developer")),
) -> dict:
    try:
        stop_authoring_run(uuid.UUID(authoring_run_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except TimeoutError:
        # stop_event is already set at this point — the background thread
        # only checks it between phases (replay/extract/brain/golden/
        # certifying), never mid-phase, so a brain-phase repair against a
        # real portal can legitimately run past the 30s wait here. The stop
        # request is still durable: it takes effect at the next checkpoint.
        return {"ok": True, "status": "stopping"}
    return {"ok": True}


@app.get("/recordings/{recording_id}/export-for-authoring")
def export_recording_for_authoring_endpoint(
    recording_id: str, _user: CurrentUser = Depends(require_job_function("developer")),
) -> dict:
    """Everything the authoring pipeline needs to run against this
    recording, for a remote client (the desktop exe's local scratch
    environment) to import and author from locally — see
    atlas_engine.authoring_export and desktop/local_authoring.py."""
    try:
        return export_recording_for_authoring(uuid.UUID(recording_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


class SubmitAuthoringResultResponse(BaseModel):
    golden_script_id: str
    authoring_run_id: str


@app.post("/recordings/{recording_id}/submit-authoring-result", response_model=SubmitAuthoringResultResponse)
async def submit_authoring_result_endpoint(
    recording_id: str,
    golden_status: str = Form(...),
    script_text: str = Form(...),
    certification_runs: str = Form(...),
    events: str = Form(...),
    repair_attempt: int = Form(0),
    evidence_files: list[UploadFile] = File(default=[]),
    _user: CurrentUser = Depends(require_job_function("developer")),
) -> SubmitAuthoringResultResponse:
    """Accepts the final result of an authoring run that ran on a remote
    client's own machine (the desktop exe's local scratch environment,
    per docs/superpowers/specs/2026-08-17-desktop-exe-packaging-design.md)
    and persists it exactly as a locally-run authoring run would, on this
    deployed backend's own database and blob store. The actual
    replay/brain/certify work already happened on the client's machine —
    it has to; authoring replays against the real target portal, the same
    live-network requirement recording has. This endpoint only records
    the outcome.

    evidence_files are matched to certification runs by filename
    ("evidence-<run_number>.png") rather than position, since a client
    only uploads evidence for the runs that actually failed.
    """
    with session_scope() as session:
        recording = session.get(Recording, uuid.UUID(recording_id))
        if recording is None:
            raise HTTPException(status_code=404, detail=f"No such recording: {recording_id}")
        org_id = recording.org_id
        portal_profile_id = recording.portal_profile_id
        request_id = recording.request_id

    store = get_store()
    run_dir = store.new_run_dir(prefix="goldens")
    script_blob_ref = run_dir.write_text("golden.py", script_text)

    evidence_by_run: dict[int, str] = {}
    for upload in evidence_files:
        name = upload.filename or ""
        try:
            run_number = int(name.removeprefix("evidence-").removesuffix(".png"))
        except ValueError:
            continue
        evidence_by_run[run_number] = run_dir.write_bytes(name, await upload.read())

    with session_scope() as session:
        golden = GoldenScript(
            org_id=org_id, portal_profile_id=portal_profile_id,
            version=next_golden_script_version(session, portal_profile_id),
            script_blob_ref=script_blob_ref, status=golden_status,
            created_from_recording_id=uuid.UUID(recording_id),
        )
        session.add(golden)
        session.flush()
        golden_script_id = golden.id

        authoring_run = AuthoringRun(
            org_id=org_id, recording_id=uuid.UUID(recording_id), golden_script_id=golden_script_id,
            status="completed", current_phase="done", repair_attempt=repair_attempt,
        )
        session.add(authoring_run)
        session.flush()
        authoring_run_id = authoring_run.id

        for event in json.loads(events):
            session.add(AuthoringRunEvent(
                authoring_run_id=authoring_run_id, phase=event["phase"], step_index=event.get("step_index"),
                level=event.get("level", "info"), message=event["message"],
            ))

        for run in json.loads(certification_runs):
            session.add(CertificationRun(
                authoring_run_id=authoring_run_id, run_number=run["run_number"], status=run["status"],
                failure_evidence_blob_ref=evidence_by_run.get(run["run_number"]),
            ))

        if golden_status == "certified" and request_id is not None:
            request = session.get(RecordingRequest, request_id)
            if request is not None:
                request.status = "fulfilled"
                request.fulfilled_golden_script_id = golden_script_id

    return SubmitAuthoringResultResponse(golden_script_id=str(golden_script_id), authoring_run_id=str(authoring_run_id))


class LoginBody(BaseModel):
    email: str
    password: str


class MeResponse(BaseModel):
    user_id: str
    org_id: str
    email: str
    role: str
    job_function: str | None = None


def _me_response_for(user: User) -> MeResponse:
    return MeResponse(
        user_id=str(user.id), org_id=str(user.org_id), email=user.email,
        role=user.role, job_function=user.job_function,
    )


@app.post("/auth/login", response_model=MeResponse)
def login(body: LoginBody, response: Response) -> MeResponse:
    with session_scope() as session:
        user = session.query(User).filter_by(email=body.email).one_or_none()
        if user is None or not verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")

        token = create_session_token({"user_id": str(user.id), "org_id": str(user.org_id)})
        response.set_cookie(SESSION_COOKIE_NAME, token, httponly=True, samesite="lax")
        return _me_response_for(user)


@app.post("/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@app.get("/auth/me", response_model=MeResponse)
def me(atlas_session: str | None = Cookie(default=None)) -> MeResponse:
    if atlas_session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = verify_session_token(atlas_session)
    if payload is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    with session_scope() as session:
        user = session.get(User, uuid.UUID(payload["user_id"]))
        if user is None:
            raise HTTPException(status_code=401, detail="User no longer exists")
        return _me_response_for(user)


class StartRecordingBody(BaseModel):
    org_id: str
    start_url: str
    request_id: str | None = None
    role: str = "demo"
    user_id: str | None = None


class StartRecordingResponse(BaseModel):
    capture_session_id: str


class StopRecordingResponse(BaseModel):
    recording_id: str


@app.get("/recordings")
def list_recordings_endpoint(
    status: str | None = None, user_id: str | None = None,
    lob: str | None = None, action: str | None = None, payer_id: str | None = None,
    _user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    # org_id and, for an operations agent, user_id are always derived from
    # the authenticated session rather than trusted from the client — a
    # client-supplied value for either would otherwise let one org see
    # another's recordings, or one agent see another agent's.
    effective_user_id = user_id
    if _user.job_function == "operations_agent" and _user.role not in ("owner", "admin"):
        effective_user_id = str(_user.id)
    return list_recordings(
        str(_user.org_id), status=status, user_id=effective_user_id,
        lob=lob, action=action, payer_id=payer_id,
    )


@app.post("/recordings/start", response_model=StartRecordingResponse)
def start_recording_endpoint(body: StartRecordingBody) -> StartRecordingResponse:
    capture_session_id = start_live_capture(
        body.org_id, body.start_url,
        request_id=uuid.UUID(body.request_id) if body.request_id else None,
        role=body.role, user_id=body.user_id,
    )
    return StartRecordingResponse(capture_session_id=str(capture_session_id))


@app.post("/recordings/{capture_session_id}/stop", response_model=StopRecordingResponse)
def stop_recording_endpoint(capture_session_id: str) -> StopRecordingResponse:
    try:
        recording_id = stop_live_capture(uuid.UUID(capture_session_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        # A browser-launch/navigation failure inside the live-capture
        # background thread (live_capture.py's _LiveCaptureSession._run)
        # only ever surfaces here, when stop() re-raises self.error.
        # unhandled_exception_handler (above) deliberately hides exception
        # detail from every OTHER endpoint's caller, which is right for a
        # shared multi-tenant backend -- but this endpoint's caller is the
        # single person running the desktop exe, who has no other way to
        # find out what actually went wrong (the full traceback is already
        # logged; see observability.configure_logging's ATLAS_LOG_FILE for
        # where a packaged .app's build of that log ends up).
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")
    return StopRecordingResponse(recording_id=str(recording_id))


class SubmitCaptureResponse(BaseModel):
    recording_id: str


@app.post("/recordings/submit-capture", response_model=SubmitCaptureResponse)
async def submit_capture_endpoint(
    start_url: str = Form(...),
    actions: str = Form(...),
    request_id: str | None = Form(None),
    role: str = Form("demo"),
    video_started_at_ms: int | None = Form(None),
    video_ended_at_ms: int | None = Form(None),
    codegen_script: str | None = Form(None),
    page_extractions: str | None = Form(None),
    video: UploadFile | None = File(None),
    trace: UploadFile | None = File(None),
    har: UploadFile | None = File(None),
    current_user: CurrentUser = Depends(get_current_user),
) -> SubmitCaptureResponse:
    """Accepts a recording captured on a remote client's own machine (the
    desktop exe's local browser-automation step) and persists it exactly
    as persist_recording does for a capture that ran against this same
    process — used when the desktop exe submits to this deployed backend
    instead of writing to a database/blob store directly, so agent
    machines never need database credentials. org_id and user_id are
    intentionally NOT accepted from the client; they come from the
    authenticated session so a client can't submit into another org.

    Multi-tab recordings (additional_pages) aren't supported through this
    endpoint yet — only the main page's actions/video/artifacts.
    """
    parsed_actions = [RecordedAction(**item) for item in json.loads(actions)]
    parsed_extractions = json.loads(page_extractions) if page_extractions else None

    with tempfile.TemporaryDirectory(prefix="atlas-submit-capture-") as tmp_dir:
        tmp_path = Path(tmp_dir)

        video_path = None
        if video is not None:
            video_path = tmp_path / "video.webm"
            video_path.write_bytes(await video.read())

        capture_artifacts = []
        if trace is not None:
            trace_path = tmp_path / "trace.zip"
            trace_path.write_bytes(await trace.read())
            capture_artifacts.append(("trace.zip", trace_path))
        if har is not None:
            har_path = tmp_path / "network.har"
            har_path.write_bytes(await har.read())
            capture_artifacts.append(("network.har", har_path))

        recording_id = persist_recording(
            str(current_user.org_id), start_url, parsed_actions,
            request_id=uuid.UUID(request_id) if request_id else None,
            role=role, user_id=str(current_user.id),
            video_path=video_path,
            video_started_at_ms=video_started_at_ms,
            video_ended_at_ms=video_ended_at_ms,
            page_extractions=parsed_extractions,
            codegen_script=codegen_script,
            capture_artifacts=capture_artifacts or None,
        )

    return SubmitCaptureResponse(recording_id=str(recording_id))


class RecordingStepResponse(BaseModel):
    order_index: int
    caption: str | None = None
    offset_ms: int | None = None


class RecordingPageResponse(BaseModel):
    page_index: int
    has_video: bool
    video_asset_id: str | None = None
    duration_seconds: int | None = None
    steps: list[RecordingStepResponse]


class RecordingDetailResponse(BaseModel):
    id: str
    status: str
    role: str
    title: str | None = None
    description: str | None = None
    latest_authoring_run_id: str | None = None
    codegen_script: str | None = None
    pages: list[RecordingPageResponse]
    # Recording form v1 — lob/action from the linked request (Section A),
    # plus this recording's own saved Section B values (pre-fills the
    # review page's form on re-open). See
    # docs/superpowers/specs/2026-08-19-recording-form-v1-design.md
    lob: str | None = None
    action: str | None = None
    payer_scope: str | None = None
    payer_ids: list[str] | None = None
    other_system_ids: list[str] | None = None
    carc_code_id: str | None = None
    code_free_text: str | None = None
    sub_reason: str | None = None
    resolution_path: list[str] | None = None
    evidence_required: list[str] | None = None
    report_id: str | None = None
    date_param_logic: str | None = None
    output_destination: str | None = None
    post_processing: str | None = None
    scenario_description: str | None = None


class PageExtractionResponse(BaseModel):
    id: str
    page_index: int
    url: str
    data: dict
    extracted_at: str


@app.get("/recordings/{recording_id}", response_model=RecordingDetailResponse)
def get_recording_endpoint(recording_id: str) -> RecordingDetailResponse:
    try:
        detail = get_recording_detail(uuid.UUID(recording_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return RecordingDetailResponse(**detail)


class UpdateRecordingDetailsBody(BaseModel):
    title: str | None = None
    description: str | None = None
    # Recording form v1, Section B — see
    # docs/superpowers/specs/2026-08-19-recording-form-v1-design.md
    payer_scope: str | None = None
    payer_ids: list[str] | None = None
    other_system_ids: list[str] | None = None
    carc_code_id: str | None = None
    code_free_text: str | None = None
    sub_reason: str | None = None
    resolution_path: list[str] | None = None
    evidence_required: list[str] | None = None
    report_id: str | None = None
    date_param_logic: str | None = None
    output_destination: str | None = None
    post_processing: str | None = None
    scenario_description: str | None = None


@app.patch("/recordings/{recording_id}")
def update_recording_details_endpoint(recording_id: str, body: UpdateRecordingDetailsBody) -> dict:
    try:
        carc_code_is_multi_use = False
        if body.carc_code_id is not None:
            with session_scope() as session:
                from atlas_engine.db.models import CarcCode
                carc_code = session.get(CarcCode, uuid.UUID(body.carc_code_id))
                carc_code_is_multi_use = carc_code is not None and carc_code.is_multi_use

        validate_payers_required(body.payer_scope, body.payer_ids)
        validate_sub_reason_required(body.sub_reason, carc_code_is_multi_use)
        validate_date_param_logic(body.date_param_logic)
        validate_post_processing(body.post_processing)

        update_recording_details(
            uuid.UUID(recording_id), title=body.title, description=body.description,
            payer_scope=body.payer_scope, payer_ids=body.payer_ids,
            other_system_ids=body.other_system_ids, carc_code_id=body.carc_code_id,
            code_free_text=body.code_free_text, sub_reason=body.sub_reason,
            resolution_path=body.resolution_path, evidence_required=body.evidence_required,
            report_id=body.report_id, date_param_logic=body.date_param_logic,
            output_destination=body.output_destination, post_processing=body.post_processing,
            scenario_description=body.scenario_description,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if detail.startswith("No such recording") else 422
        raise HTTPException(status_code=status_code, detail=detail)
    return {"ok": True}


class SubmitRecordingBody(BaseModel):
    submitted_by: str


class SubmitRecordingResponse(BaseModel):
    request_id: str


@app.post("/recordings/{recording_id}/submit", response_model=SubmitRecordingResponse)
def submit_recording_endpoint(
    recording_id: str, body: SubmitRecordingBody,
    _user: CurrentUser = Depends(require_job_function("operations_agent")),
) -> SubmitRecordingResponse:
    try:
        request_id = submit_recording_for_authoring(uuid.UUID(recording_id), uuid.UUID(body.submitted_by))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return SubmitRecordingResponse(request_id=str(request_id))


@app.get("/recordings/{recording_id}/video/{page_index}")
def get_recording_video_endpoint(recording_id: str, page_index: int):
    with session_scope() as session:
        video_asset = (
            session.query(VideoAsset)
            .filter_by(recording_id=uuid.UUID(recording_id), page_index=page_index)
            .one_or_none()
        )
        if video_asset is None:
            raise HTTPException(status_code=404, detail="No video for this recording page")
        storage_ref = video_asset.storage_ref

    store = get_store()
    url = store.url_for(storage_ref)
    if url is not None:
        return RedirectResponse(url)

    try:
        video_path = store.local_path_for(storage_ref)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Video file missing on disk")

    return FileResponse(video_path, media_type="video/webm")


@app.get("/recordings/{recording_id}/extractions", response_model=list[PageExtractionResponse])
def list_extractions_endpoint(recording_id: str) -> list[PageExtractionResponse]:
    with session_scope() as session:
        if session.get(Recording, uuid.UUID(recording_id)) is None:
            raise HTTPException(status_code=404, detail=f"No such recording: {recording_id}")
    return [PageExtractionResponse(**e) for e in list_page_extractions(uuid.UUID(recording_id))]
