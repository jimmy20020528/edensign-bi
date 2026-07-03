"""Reverse-proxy for the Wizard's orchestration endpoints.

The Listing Wizard front-end calls a few endpoints that physically live on the
agent/tool service (port 8002): /classify-rooms, /generate-listing,
/pipeline/run, /v2/pipeline/run. In the RunPod demo only port 8000 (this BI
service) is exposed
through the public proxy, so we forward those calls to the local agent service
over localhost. This keeps the whole wizard reachable through a single port.
"""
from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Request, Response

router = APIRouter()

# Upstream services. bi (:8000) is the single public gateway; it forwards each
# path to whichever internal service owns it, so the whole API is reachable on one
# port / one pod.
AGENT_BASE = os.getenv("AGENT_BASE", "http://localhost:8002")
CV_MODELS_BASE = os.getenv("CV_MODELS_BASE", "http://localhost:8003")
HOME_REPORT_BASE = os.getenv("HOME_REPORT_BASE", "http://localhost:8001")

# Long timeout: /pipeline/run fans out to CV + home-report + BI + GPT.
_TIMEOUT = httpx.Timeout(300.0)

_HOP_BY_HOP = {
    "host", "content-length", "connection", "keep-alive",
    "transfer-encoding", "upgrade",
}


async def _proxy(request: Request, path: str, base: str = AGENT_BASE) -> Response:
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        upstream = await client.request(
            request.method,
            f"{base}{path}",
            content=body,
            headers=headers,
            params=dict(request.query_params),
        )
    resp_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )


@router.post("/classify-rooms")
async def proxy_classify_rooms(request: Request) -> Response:
    return await _proxy(request, "/classify-rooms")


@router.post("/generate-listing")
async def proxy_generate_listing(request: Request) -> Response:
    return await _proxy(request, "/generate-listing")


@router.post("/pipeline/run")
async def proxy_pipeline_run(request: Request) -> Response:
    return await _proxy(request, "/pipeline/run")


@router.post("/v2/pipeline/run")
async def proxy_pipeline_run_v2(request: Request) -> Response:
    return await _proxy(request, "/v2/pipeline/run")


@router.post("/walkthrough")
async def proxy_walkthrough(request: Request) -> Response:
    # photo walk-through ordering lives on cv-models
    return await _proxy(request, "/walkthrough", base=CV_MODELS_BASE)


@router.post("/report")
async def proxy_report(request: Request) -> Response:
    # per-photo quality/condition report lives on home-report-ai
    return await _proxy(request, "/report", base=HOME_REPORT_BASE)
