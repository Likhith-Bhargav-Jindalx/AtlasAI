import uuid
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from atlas_engine.observability import RequestIdMiddleware, configure_logging

from atlas_engine.auth.deps import CurrentUser, get_current_user, require_job_function
from atlas_engine.auth.passwords import verify_password
from atlas_engine.auth.session import create_session_token, verify_session_token
from atlas_engine.authoring.run import resume_authoring_run, start_authoring_run, stop_authoring_run
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
    User,
    VideoAsset,
)
from atlas_engine.live_capture import start_live_capture, stop_live_capture
from atlas_engine.llm.factory import create_llm_client
from atlas_engine.llm.usage import list_llm_usage_summary
from atlas_engine.recording_service import (
    claim_recording_request,
    create_recording_request,
    get_recording_detail,
    list_page_extractions,
    list_recording_requests,
    list_recordings,
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
        entry = next(
            (c for c in heal_event.diff_summary.get("changes", []) if c["step_index"] == step_index), None,
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


class RecordingRequestCreatedResponse(BaseModel):
    request_id: str


class ClaimRequestBody(BaseModel):
    developer_id: str


@app.post("/recording-requests", response_model=RecordingRequestCreatedResponse)
def create_recording_request_endpoint(body: CreateRecordingRequestBody) -> RecordingRequestCreatedResponse:
    request_id = create_recording_request(
        body.org_id, body.requested_by, body.target_url, body.title, body.description
    )
    return RecordingRequestCreatedResponse(request_id=str(request_id))


@app.get("/recording-requests")
def list_recording_requests_endpoint(org_id: str, status: str | None = None) -> list[dict]:
    return list_recording_requests(org_id, status=status)


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
def list_recordings_endpoint(org_id: str, status: str | None = None, user_id: str | None = None) -> list[dict]:
    return list_recordings(org_id, status=status, user_id=user_id)


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
    recording_id = stop_live_capture(uuid.UUID(capture_session_id))
    return StopRecordingResponse(recording_id=str(recording_id))


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


@app.patch("/recordings/{recording_id}")
def update_recording_details_endpoint(recording_id: str, body: UpdateRecordingDetailsBody) -> dict:
    try:
        update_recording_details(uuid.UUID(recording_id), title=body.title, description=body.description)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
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
