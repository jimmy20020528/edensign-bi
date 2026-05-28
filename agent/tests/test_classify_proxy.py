"""Test agent /classify-rooms proxy endpoint."""
import io
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.server import app


def make_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), (200, 100, 50)).save(buf, format="JPEG")
    return buf.getvalue()


def test_classify_rooms_proxies_to_cv_models():
    mock_response = {
        "photos": [{"index": 0, "room_type": "kitchen", "occupancy": "furnished",
                    "confidence": 0.9, "group_id": 1}],
        "groups": [{"group_id": 1, "room_type": "kitchen", "occupancy": "furnished",
                    "photo_indices": [0]}],
    }
    with patch("tools.server.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_response_obj = AsyncMock()
        mock_response_obj.status_code = 200
        mock_response_obj.json = lambda: mock_response  # sync method, not async
        mock_client.post = AsyncMock(return_value=mock_response_obj)

        client = TestClient(app)
        r = client.post(
            "/classify-rooms",
            files=[("files", ("img.jpg", make_jpeg(), "image/jpeg"))],
        )

    assert r.status_code == 200
    data = r.json()
    assert "photos" in data and "groups" in data


def test_classify_rooms_returns_503_when_cv_models_down():
    import httpx
    with patch("tools.server.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectError("connection refused")

        client = TestClient(app)
        r = client.post(
            "/classify-rooms",
            files=[("files", ("img.jpg", make_jpeg(), "image/jpeg"))],
        )

    assert r.status_code == 503
    assert r.json()["detail"] == "classification_unavailable"
