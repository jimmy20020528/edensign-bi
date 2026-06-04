# agent/tests/test_generate_listing.py
"""Tests for agent /generate-listing (on-demand, per-style)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

HOME_REPORT = {
    "rooms": [
        {"room_type": "kitchen", "quality_decimal": 5.2, "quality_rating": "Q2",
         "condition_rating": "C2", "quality_rationale": "updated quartz counters"},
    ]
}


def _client():
    import importlib
    import tools.server as srv
    importlib.reload(srv)
    from fastapi.testclient import TestClient
    return srv, TestClient(srv.app)


def test_generate_listing_returns_text_and_style():
    srv, client = _client()
    resp = MagicMock(); resp.status_code = 200
    resp.json.return_value = {"full_body": "A beautiful Modern home..."}
    with patch.object(srv.httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=resp):
        r = client.post("/generate-listing",
                        json={"style": "Modern", "home_report": HOME_REPORT, "zipcode": "02134"})
    assert r.status_code == 200
    data = r.json()
    assert data["style"] == "Modern"
    assert "Modern" in data["listing_text"]


def test_generate_listing_passes_style_and_highlights_to_bi():
    srv, client = _client()
    resp = MagicMock(); resp.status_code = 200
    resp.json.return_value = {"full_body": "text"}
    mock_post = AsyncMock(return_value=resp)
    with patch.object(srv.httpx.AsyncClient, "post", mock_post):
        client.post("/generate-listing",
                    json={"style": "Coastal", "home_report": HOME_REPORT, "zipcode": "02134"})
    payload = mock_post.call_args.kwargs["json"]
    assert payload["style"] == "Coastal"
    assert "kitchen" in payload["additional_requirements"]


def test_generate_listing_empty_style_400():
    srv, client = _client()
    r = client.post("/generate-listing", json={"style": "", "home_report": None})
    assert r.status_code == 400


def test_generate_listing_no_home_report_ok():
    srv, client = _client()
    resp = MagicMock(); resp.status_code = 200
    resp.json.return_value = {"full_body": "generic text"}
    with patch.object(srv.httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=resp):
        r = client.post("/generate-listing", json={"style": "Modern", "home_report": None})
    assert r.status_code == 200


def test_generate_listing_bi_error_502():
    srv, client = _client()
    resp = MagicMock(); resp.status_code = 500; resp.text = "boom"
    with patch.object(srv.httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=resp):
        r = client.post("/generate-listing", json={"style": "Modern", "home_report": None})
    assert r.status_code == 502


def test_pipeline_run_no_longer_generates_listing():
    srv, client = _client()
    posted = []

    async def fake_post(self, url, **kwargs):
        posted.append(url)
        resp = MagicMock(); resp.status_code = 200
        if url.endswith("/report"):
            resp.json.return_value = {"rooms": []}
        elif "explain" in url:
            resp.json.return_value = {"analysis": {}, "llm": {}}
        else:
            resp.json.return_value = {}
        return resp

    async def fake_get(self, url, **kwargs):
        resp = MagicMock(); resp.status_code = 200
        resp.json.return_value = {"zipcode": "02134",
                                  "recommended_styles": [{"style": "Modern"}]}
        return resp

    with patch.object(srv.httpx.AsyncClient, "post", new=fake_post), \
         patch.object(srv.httpx.AsyncClient, "get", new=fake_get):
        r = client.post(
            "/pipeline/run",
            files=[("files", ("a.jpg", b"x", "image/jpeg"))],
            data={"zipcode": "02134"},
        )

    assert r.status_code == 200
    body = r.json()
    assert body["listing_text"] is None
    assert not any("/listing/write" in u for u in posted)


def test_generate_listing_forwards_template_and_why():
    srv, client = _client()
    resp = MagicMock(); resp.status_code = 200
    resp.json.return_value = {"full_body": "text", "why_summary": "ws", "why_steps": {"style": "Story"}}
    mock_post = AsyncMock(return_value=resp)
    with patch.object(srv.httpx.AsyncClient, "post", mock_post):
        r = client.post("/generate-listing", json={
            "style": "Modern", "template": "story", "home_report": {"rooms": []}})
    payload = mock_post.call_args.kwargs["json"]
    assert payload["template"] == "story"
    assert payload["home_report"] == {"rooms": []}
    resp_body = r.json()
    assert resp_body["why_summary"] == "ws"
    assert resp_body["why_steps"] == {"style": "Story"}
    assert resp_body["template"] == "story"
