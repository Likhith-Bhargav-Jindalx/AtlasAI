"""Built by desktop/app.py instead of importing atlas_engine.api directly
when ATLAS_REMOTE_BACKEND_URL is set: reverse-proxies every request to the
deployed backend except the endpoints that must run on this machine —
starting/stopping the local browser-automation capture, and running the
authoring pipeline (replay/brain/golden/certify also need a live browser
against the real target portal). Both a finished capture and a finished
authoring run are submitted to the deployed backend afterward
(atlas_engine.remote_submit / desktop.local_authoring) instead of being
written to a local database/blob store directly — this process never
holds real database or storage credentials.

The proxied routes are an explicit, manually-maintained list mirroring
atlas_engine/api.py's own route table, not a blanket catch-all — a new
top-level path added to that module later needs a matching line added
here too. This trades a small amount of upkeep for not having to guess
which arbitrary paths are "API calls to proxy" vs. "static frontend
assets to serve locally" — see the two-request-origin note in
desktop/app.py for why that distinction matters at all.
"""

import functools
import uuid

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun, AuthoringRunEvent, CertificationRun, GoldenScript
from atlas_engine.live_capture import start_live_capture, stop_live_capture
from atlas_engine.remote_submit import submit_capture_to_remote
from atlas_engine.scratch_env import activate_scratch_environment
from atlas_engine.storage import get_store

# A plain sibling-module import, not desktop.local_authoring — see
# app.py's note on why (this module is itself only ever imported that
# same way, as a sibling of app.py, not as a package attribute).
from local_authoring import resume_local_authoring, start_local_authoring, stop_local_authoring, submit_if_terminal

# Response headers that describe the *proxy's own* transport framing, not
# the upstream content — relaying them verbatim alongside a body httpx has
# already decompressed/reassembled would corrupt the response.
_STRIP_RESPONSE_HEADERS = {"content-encoding", "content-length", "transfer-encoding"}

# Mirrors atlas_engine/api.py's own top-level route paths, minus /health,
# the two capture endpoints, and the three authoring-run endpoints below
# (all of which run locally, not proxied). A new top-level path added
# there needs a matching line added here.
_PROXY_PATH_TEMPLATES = [
    "/author",
    "/golden-scripts/{path:path}",
    "/heal-events",
    "/heal-events/{path:path}",
    "/recording-requests",
    "/recording-requests/{path:path}",
    "/llm-usage/{path:path}",
    "/auth/{path:path}",
    "/recordings",
    "/recordings/{path:path}",  # registered last: /recordings/start and /stop above already win
]


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


class StartAuthoringResponse(BaseModel):
    authoring_run_id: str


def build_app(remote_backend_url: str) -> FastAPI:
    # Every atlas_engine call this process ever makes for authoring goes
    # to this scratch environment — see the module docstring there for
    # why that's safe here specifically (this process never needs its
    # *real* database/storage settings for anything else; recording
    # capture doesn't touch atlas_engine.db/storage at all in this mode).
    activate_scratch_environment()

    app = FastAPI(title="Atlas Desktop (remote-submit mode)")
    client = httpx.Client(base_url=remote_backend_url, timeout=30.0)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/recordings/start", response_model=StartRecordingResponse)
    def start_recording_endpoint(body: StartRecordingBody, request: Request) -> StartRecordingResponse:
        persist_fn = functools.partial(
            submit_capture_to_remote, base_url=remote_backend_url, cookies=dict(request.cookies),
        )
        capture_session_id = start_live_capture(
            body.org_id, body.start_url,
            request_id=uuid.UUID(body.request_id) if body.request_id else None,
            role=body.role, user_id=body.user_id, persist_fn=persist_fn,
        )
        return StartRecordingResponse(capture_session_id=str(capture_session_id))

    @app.post("/recordings/{capture_session_id}/stop", response_model=StopRecordingResponse)
    def stop_recording_endpoint(capture_session_id: str) -> StopRecordingResponse:
        recording_id = stop_live_capture(uuid.UUID(capture_session_id))
        return StopRecordingResponse(recording_id=str(recording_id))

    @app.post("/recordings/{recording_id}/author", response_model=StartAuthoringResponse)
    def start_authoring_endpoint(recording_id: str, request: Request) -> StartAuthoringResponse:
        try:
            authoring_run_id = start_local_authoring(remote_backend_url, dict(request.cookies), recording_id)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
        return StartAuthoringResponse(authoring_run_id=str(authoring_run_id))

    @app.get("/authoring-runs/{authoring_run_id}")
    def get_authoring_run_endpoint(authoring_run_id: str) -> dict:
        """Same response shape as atlas_engine.api's own endpoint of the
        same name, built by hand here rather than imported — this reads
        the scratch environment's database directly (session_scope()
        already resolves there in this process), not anything proxied."""
        try:
            run_uuid = uuid.UUID(authoring_run_id)
        except ValueError:
            raise HTTPException(status_code=404, detail=f"No such authoring run: {authoring_run_id}")

        submit_if_terminal(run_uuid)  # cheap no-op unless this run just finished and hasn't been submitted yet

        with session_scope() as session:
            run = session.get(AuthoringRun, run_uuid)
            if run is None:
                raise HTTPException(status_code=404, detail=f"No such authoring run: {authoring_run_id}")

            events = session.query(AuthoringRunEvent).filter_by(authoring_run_id=run.id).order_by(AuthoringRunEvent.seq).all()
            certification_runs = (
                session.query(CertificationRun).filter_by(authoring_run_id=run.id).order_by(CertificationRun.run_number).all()
            )

            golden_script = None
            if run.golden_script_id is not None:
                golden = session.get(GoldenScript, run.golden_script_id)
                if golden is not None:
                    store = get_store()
                    script_text = store.read_text(golden.script_blob_ref) if store.exists(golden.script_blob_ref) else None
                    golden_script = {
                        "id": str(golden.id), "version": golden.version, "status": golden.status,
                        "script_text": script_text,
                    }

            return {
                "id": str(run.id),
                "status": run.status,
                "current_phase": run.current_phase,
                "repair_attempt": run.repair_attempt,
                "events": [
                    {"phase": e.phase, "step_index": e.step_index, "level": e.level, "message": e.message}
                    for e in events
                ],
                "golden_script": golden_script,
                "certification_runs": [
                    {"run_number": c.run_number, "status": c.status, "failure_evidence_blob_ref": c.failure_evidence_blob_ref}
                    for c in certification_runs
                ],
            }

    @app.post("/authoring-runs/{authoring_run_id}/resume")
    def resume_authoring_run_endpoint(authoring_run_id: str) -> dict:
        try:
            resume_local_authoring(uuid.UUID(authoring_run_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {"ok": True}

    @app.post("/authoring-runs/{authoring_run_id}/stop")
    def stop_authoring_run_endpoint(authoring_run_id: str) -> dict:
        try:
            stop_local_authoring(uuid.UUID(authoring_run_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except TimeoutError:
            return {"ok": True, "status": "stopping"}
        return {"ok": True}

    async def _proxy(request: Request) -> Response:
        """Forwards the incoming request's own path+query verbatim to the
        remote backend — deliberately ignores any path parameter FastAPI
        captured for the matched template, since the raw request path is
        already exactly what needs to be forwarded."""
        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}

        upstream = client.request(
            request.method,
            httpx.URL(path=request.url.path, query=request.url.query.encode("utf-8")),
            content=body, headers=headers, cookies=request.cookies,
        )
        response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _STRIP_RESPONSE_HEADERS}
        return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)

    for template in _PROXY_PATH_TEMPLATES:
        app.add_api_route(template, _proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])

    return app
