# agent/tests/test_classify_serverless.py
"""Tests for agent /classify-rooms serverless routing (URL-based)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


CLASSIFY_FIXTURE = {
    "photos": [{"index": 0, "room_type": "kitchen", "occupancy": "furnished",
                "confidence": 0.9, "group_id": 1}],
    "groups": [{"group_id": 1, "room_type": "kitchen",
                "occupancy": "furnished", "photo_indices": [0]}],
}

TEST_URLS = [
    "https://content.edensign.io/images/test-photo.jpg",
]


def test_classify_rooms_calls_serverless(monkeypatch):
    """With CV_SERVERLESS_ID set, /classify-rooms POSTs URLs to RunPod API."""
    import importlib
    import tools.server as srv

    monkeypatch.setenv("CV_SERVERLESS_ID", "ep-test-123")
    monkeypatch.setenv("RUNPOD_API_KEY", "rp-key-xyz")
    importlib.reload(srv)

    from fastapi.testclient import TestClient
    client = TestClient(srv.app)

    runpod_response = {"id": "sync-1", "status": "COMPLETED",
                       "output": CLASSIFY_FIXTURE}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = runpod_response

    with patch.object(srv.httpx.AsyncClient, "post", new_callable=AsyncMock,
                      return_value=mock_resp):
        r = client.post(
            "/classify-rooms",
            json={"image_urls": TEST_URLS},
        )

    assert r.status_code == 200
    data = r.json()
    assert "photos" in data
    assert data["photos"][0]["room_type"] == "kitchen"


def test_classify_rooms_empty_urls():
    """Empty image_urls returns 400."""
    import importlib
    import tools.server as srv
    importlib.reload(srv)

    from fastapi.testclient import TestClient
    client = TestClient(srv.app)
    r = client.post("/classify-rooms", json={"image_urls": []})
    assert r.status_code == 400


def test_classify_rooms_serverless_payload_contains_urls(monkeypatch):
    """Verify RunPod payload uses url field, not base64 data."""
    import importlib
    import tools.server as srv

    monkeypatch.setenv("CV_SERVERLESS_ID", "ep-test-123")
    monkeypatch.setenv("RUNPOD_API_KEY", "rp-key-xyz")
    importlib.reload(srv)

    from fastapi.testclient import TestClient
    client = TestClient(srv.app)

    captured = {}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "x", "status": "COMPLETED", "output": CLASSIFY_FIXTURE}

    async def capture_post(url, **kwargs):
        captured["json"] = kwargs.get("json", {})
        return mock_resp

    with patch.object(srv.httpx.AsyncClient, "post", new_callable=AsyncMock, side_effect=capture_post):
        client.post("/classify-rooms", json={"image_urls": TEST_URLS})

    images = captured["json"]["input"]["images"]
    assert len(images) == 1
    assert "url" in images[0]
    assert "data" not in images[0]
    assert images[0]["url"] == TEST_URLS[0]
