# app/tests/test_wizard_proxy.py
"""Tests for the bi:80 gateway's reverse proxy to the agent service.

wizard_proxy.py is an explicit allowlist of paths the public gateway (:80)
forwards to the internal agent service (:8002) — agent registering a route
does not make it reachable through the gateway on its own. This caught a real
bug: /v2/pipeline/run was added to agent/tools/server.py but never added
here, so the gateway 404'd on it in production even though agent itself
served it fine.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.wizard_proxy as wizard_proxy


def _client():
    app = FastAPI()
    app.include_router(wizard_proxy.router)
    return TestClient(app)


def test_v2_pipeline_run_is_proxied_to_agent():
    client = _client()
    resp = MagicMock()
    resp.status_code = 200
    resp.content = b'{"n_photos": 1}'
    resp.headers = {"content-type": "application/json"}

    with patch.object(wizard_proxy.httpx.AsyncClient, "request",
                       new=AsyncMock(return_value=resp)) as mock_request:
        r = client.post("/v2/pipeline/run", json={"image_urls": ["http://x/a.jpg"], "zipcode": "02135"})

    assert r.status_code == 200
    assert r.json() == {"n_photos": 1}
    called_url = mock_request.call_args.args[1]
    assert called_url == f"{wizard_proxy.AGENT_BASE}/v2/pipeline/run"
