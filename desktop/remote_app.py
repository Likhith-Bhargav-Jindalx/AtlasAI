"""Built by desktop/app.py instead of importing atlas_engine.api directly
when ATLAS_REMOTE_BACKEND_URL is set: reverse-proxies every request to the
deployed backend except the two endpoints that must run on this machine
(starting/stopping the local browser-automation capture). A finished
capture is submitted to the deployed backend afterward
(atlas_engine.remote_submit.submit_capture_to_remote) instead of being
written to a local database/blob store directly.

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
from fastapi import FastAPI, Request
from fastapi.responses import Response
from pydantic import BaseModel

from atlas_engine.live_capture import start_live_capture, stop_live_capture
from atlas_engine.remote_submit import submit_capture_to_remote

# Response headers that describe the *proxy's own* transport framing, not
# the upstream content — relaying them verbatim alongside a body httpx has
# already decompressed/reassembled would corrupt the response.
_STRIP_RESPONSE_HEADERS = {"content-encoding", "content-length", "transfer-encoding"}

# Mirrors atlas_engine/api.py's own top-level route paths, minus /health
# and the two capture endpoints below (which run locally, not proxied).
# A new top-level path added there needs a matching line added here.
_PROXY_PATH_TEMPLATES = [
    "/author",
    "/golden-scripts/{path:path}",
    "/heal-events",
    "/heal-events/{path:path}",
    "/recording-requests",
    "/recording-requests/{path:path}",
    "/llm-usage/{path:path}",
    "/authoring-runs/{path:path}",
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


def build_app(remote_backend_url: str) -> FastAPI:
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
