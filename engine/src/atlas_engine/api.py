import uuid

from fastapi import Cookie, FastAPI, HTTPException, Response
from pydantic import BaseModel

from atlas_engine.auth.passwords import verify_password
from atlas_engine.auth.session import create_session_token, verify_session_token
from atlas_engine.authoring_service import author_from_recording
from atlas_engine.capture.events import RecordedAction
from atlas_engine.cli import author_from_recorded_actions
from atlas_engine.db import session_scope
from atlas_engine.db.models import User
from atlas_engine.live_capture import start_live_capture, stop_live_capture
from atlas_engine.llm.factory import create_llm_client
from atlas_engine.recording_service import claim_recording_request, create_recording_request, list_recording_requests
from atlas_engine.verify.heal import approve_heal_event, heal_golden_script, reject_heal_event

app = FastAPI(title="Atlas Engine Local API")

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
    llm = create_llm_client()
    result = heal_golden_script(uuid.UUID(golden_script_id), llm=llm)
    return HealResponse(**result)


@app.post("/heal-events/{heal_event_id}/approve", response_model=ApprovalResponse)
def approve_endpoint(heal_event_id: str) -> ApprovalResponse:
    result = approve_heal_event(uuid.UUID(heal_event_id))
    return ApprovalResponse(**result)


@app.post("/heal-events/{heal_event_id}/reject", response_model=ApprovalResponse)
def reject_endpoint(heal_event_id: str) -> ApprovalResponse:
    result = reject_heal_event(uuid.UUID(heal_event_id))
    return ApprovalResponse(**result)


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
def claim_recording_request_endpoint(request_id: str, body: ClaimRequestBody) -> dict:
    claim_recording_request(uuid.UUID(request_id), uuid.UUID(body.developer_id))
    return {"ok": True}


@app.post("/recordings/{recording_id}/author", response_model=AuthorResponse)
def author_from_recording_endpoint(recording_id: str) -> AuthorResponse:
    llm = create_llm_client()
    result = author_from_recording(uuid.UUID(recording_id), llm=llm)
    return AuthorResponse(**result)


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


class StartRecordingResponse(BaseModel):
    capture_session_id: str


class StopRecordingResponse(BaseModel):
    recording_id: str


@app.post("/recordings/start", response_model=StartRecordingResponse)
def start_recording_endpoint(body: StartRecordingBody) -> StartRecordingResponse:
    capture_session_id = start_live_capture(
        body.org_id, body.start_url,
        request_id=uuid.UUID(body.request_id) if body.request_id else None,
        role=body.role,
    )
    return StartRecordingResponse(capture_session_id=str(capture_session_id))


@app.post("/recordings/{capture_session_id}/stop", response_model=StopRecordingResponse)
def stop_recording_endpoint(capture_session_id: str) -> StopRecordingResponse:
    recording_id = stop_live_capture(uuid.UUID(capture_session_id))
    return StopRecordingResponse(recording_id=str(recording_id))
