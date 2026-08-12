import uuid

from fastapi import FastAPI
from pydantic import BaseModel

from atlas_engine.capture.events import RecordedAction
from atlas_engine.cli import author_from_recorded_actions
from atlas_engine.llm.factory import create_llm_client
from atlas_engine.verify.heal import approve_heal_event, heal_golden_script, reject_heal_event

app = FastAPI(title="Atlas Engine Local API")


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
