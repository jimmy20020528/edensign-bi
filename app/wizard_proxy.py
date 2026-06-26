"""Reverse-proxy for the Wizard's orchestration endpoints.

The Listing Wizard front-end calls a few endpoints that physically live on the
agent/tool service (port 8002): /classify-rooms, /generate-listing,
/pipeline/run. In the RunPod demo only port 8000 (this BI service) is exposed
through the public proxy, so we forward those calls to the local agent service
over localhost. This keeps the whole wizard reachable through a single port.
"""
from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Request, Response

router = APIRouter()

AGENT_BASE = os.getenv("AGENT_BASE", "http://localhost:8002")

# Paths that belong to the agent service, proxied verbatim.
_PROXIED_PATHS = ("/classify-rooms", "/generate-listing", "/pipeline/run")

# Long timeout: /pipeline/run fans out to CV + home-report + BI + GPT.
_TIMEOUT = httpx.Timeout(300.0)

_HOP_BY_HOP = {
    "host", "content-length", "connection", "keep-alive",
    "transfer-encoding", "upgrade",
}


async def _proxy(request: Request, path: str) -> Response:
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        upstream = await client.request(
            request.method,
            f"{AGENT_BASE}{path}",
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
