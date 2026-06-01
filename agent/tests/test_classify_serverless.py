# agent/tests/test_classify_serverless.py
"""Tests for agent /classify-rooms serverless routing."""
from __future__ import annotations

import base64
import io
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))


def _jpeg_bytes(size: tuple = (1024, 768)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=(200, 100, 50)).save(buf, format="JPEG")
    return buf.getvalue()


CLASSIFY_FIXTURE = {
    "photos": [{"index": 0, "room_type": "kitchen", "occupancy": "furnished",
                "confidence": 0.9, "group_id": 1}],
    "groups": [{"group_id": 1, "room_type": "kitchen",
                "occupancy": "furnished", "photo_indices": [0]}],
}


def test_resize_shrinks_large_image():
    from tools.server import _resize_for_dinov2
    large = _jpeg_bytes(size=(2000, 1500))
    resized = _resize_for_dinov2(large)
    img = Image.open(io.BytesIO(resized))
    assert min(img.size) <= 512


def test_resize_leaves_small_image_unchanged():
    from tools.server import _resize_for_dinov2
    small = _jpeg_bytes(size=(400, 300))
    resized = _resize_for_dinov2(small)
    img = Image.open(io.BytesIO(resized))
    assert min(img.size) == 300


def test_classify_rooms_calls_serverless(monkeypatch):
    """With CV_SERVERLESS_ID set, /classify-rooms POSTs to RunPod API."""
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
            files=[("files", ("photo.jpg", _jpeg_bytes(), "image/jpeg"))],
        )

    assert r.status_code == 200
    data = r.json()
    assert "photos" in data
    assert data["photos"][0]["room_type"] == "kitchen"
