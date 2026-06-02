# cv-models/tests/test_handler.py
"""Tests for RunPod serverless handler (URL-based)."""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import app.main as cv_main

FIXTURE = {
    "photos": [{"index": 0, "room_type": "kitchen", "occupancy": "furnished",
                "confidence": 0.9, "group_id": 1}],
    "groups": [{"group_id": 1, "room_type": "kitchen", "occupancy": "furnished",
                "photo_indices": [0]}],
}

TEST_URL = "https://content.edensign.io/images/test-photo.jpg"


@pytest.fixture(autouse=True)
def inject_state():
    cv_main._state.update({
        "ready": True, "processor": MagicMock(), "model": MagicMock(),
        "device": "cpu", "occ_clf": MagicMock(), "clf_furnished": MagicMock(),
        "clf_empty": MagicMock(), "vlad_vocab": None,
        "class_names_furnished": {"0": "kitchen"},
        "class_names_empty": {"0": "kitchen"},
        "class_names_occupancy": {"0": "empty", "1": "furnished"},
    })
    yield
    cv_main._state.clear()


def _fake_urlopen(url):
    """Return a fake HTTP response with minimal JPEG bytes."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (224, 224), color=(100, 149, 237)).save(buf, format="JPEG")
    buf.seek(0)
    return buf


def test_handler_no_images():
    from handler import handler
    result = handler({"input": {}})
    assert "error" in result


def test_handler_too_many_images():
    from handler import handler
    images = [{"url": TEST_URL, "filename": f"img{i}.jpg"} for i in range(31)]
    result = handler({"input": {"images": images}})
    assert "error" in result


def test_handler_returns_classify_result():
    from handler import handler
    images = [{"url": TEST_URL, "filename": "photo.jpg"}]
    with patch("urllib.request.urlopen", return_value=_fake_urlopen(TEST_URL)), \
         patch("app.main._classify_and_group", return_value=FIXTURE):
        result = handler({"input": {"images": images}})
    assert "photos" in result
    assert result["photos"][0]["room_type"] == "kitchen"
    assert result["groups"][0]["photo_indices"] == [0]


def test_handler_bad_url_returns_error():
    from handler import handler
    images = [{"url": "https://bad.invalid/notfound.jpg", "filename": "bad.jpg"}]
    result = handler({"input": {"images": images}})
    assert "error" in result


def test_handler_not_ready():
    from handler import handler
    cv_main._state["ready"] = False
    images = [{"url": TEST_URL, "filename": "photo.jpg"}]
    result = handler({"input": {"images": images}})
    assert "error" in result
