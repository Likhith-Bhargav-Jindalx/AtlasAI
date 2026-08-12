from fastapi import FastAPI
from pydantic import BaseModel

from atlas_engine.capture.events import RecordedAction
from atlas_engine.cli import author_from_recorded_actions

app = FastAPI(title="Atlas Engine Local API")


class AuthorRequest(BaseModel):
    org_id: str
    start_url: str
    actions: list[dict]


class AuthorResponse(BaseModel):
    status: str
    script_path: str
    golden_script_id: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/author", response_model=AuthorResponse)
def author(request: AuthorRequest) -> AuthorResponse:
    actions = [RecordedAction(**item) for item in request.actions]
    result = author_from_recorded_actions(request.org_id, request.start_url, actions)
    return AuthorResponse(**result)
