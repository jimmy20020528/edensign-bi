"""Tests for cv-models /classify-rooms endpoint.

Uses mocked model state so DINOv2 is never downloaded during tests.
TestClient without context manager skips the lifespan startup.
"""
import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))
import app.main as cv_main
from app.main import app


def make_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (224, 224), color=(100, 149, 237)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def mock_state():
    """Inject fake model state before each test."""
    cv_main._state.update({
        "ready": True,
        "processor": MagicMock(),
        "model": MagicMock(),
        "device": "cpu",
        "occ_clf": MagicMock(),
        "clf_furnished": MagicMock(),
        "clf_empty": MagicMock(),
        "class_names_furnished": {"0": "kitchen", "1": "bedroom", "2": "bathroom"},
        "class_names_empty": {"0": "kitchen", "1": "bedroom", "2": "bathroom"},
        "class_names_occupancy": {"0": "empty", "1": "furnished"},
    })
    yield
    cv_main._state.clear()


def test_health():
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_classify_rooms_too_many_files():
    client = TestClient(app)
    files = [("files", (f"img{i}.jpg", make_jpeg(), "image/jpeg")) for i in range(61)]
    r = client.post("/classify-rooms", files=files)
    assert r.status_code == 400
    assert "60" in r.json()["detail"]


def test_classify_rooms_not_ready():
    cv_main._state["ready"] = False
    client = TestClient(app)
    files = [("files", ("img.jpg", make_jpeg(), "image/jpeg"))]
    r = client.post("/classify-rooms", files=files)
    assert r.status_code == 503


def test_classify_rooms_returns_schema():
    mock_result = {
        "photos": [
            {"index": 0, "room_type": "kitchen", "occupancy": "furnished",
             "confidence": 0.92, "group_id": 1}
        ],
        "groups": [
            {"group_id": 1, "room_type": "kitchen", "occupancy": "furnished",
             "photo_indices": [0]}
        ],
    }
    client = TestClient(app)
    with patch("app.main._classify_and_group", return_value=mock_result):
        r = client.post(
            "/classify-rooms",
            files=[("files", ("img.jpg", make_jpeg(), "image/jpeg"))],
        )
    assert r.status_code == 200
    data = r.json()
    assert "photos" in data and "groups" in data
    assert data["photos"][0]["room_type"] == "kitchen"
    assert data["photos"][0]["group_id"] == 1
    assert data["groups"][0]["photo_indices"] == [0]


def test_classify_and_group_routing():
    """Verify that integer occupancy predictions are decoded and route correctly.

    Photo 0 (pred=0=empty) must use clf_empty.
    Photo 1 (pred=1=furnished) must use clf_furnished.
    """
    import numpy as np
    from unittest.mock import MagicMock, patch
    from app.main import _classify_and_group

    clf_furnished = MagicMock()
    clf_furnished.predict_proba.return_value = np.array([[0.1, 0.8, 0.1]])
    clf_furnished.classes_ = np.array([0, 1, 2])

    clf_empty = MagicMock()
    clf_empty.predict_proba.return_value = np.array([[0.7, 0.2, 0.1]])
    clf_empty.classes_ = np.array([0, 1, 2])

    occ_clf = MagicMock()
    occ_clf.predict.return_value = np.array([0, 1])  # photo 0=empty, photo 1=furnished

    cv_main._state.update({
        "ready": True,
        "processor": MagicMock(),
        "model": MagicMock(),
        "device": "cpu",
        "occ_clf": occ_clf,
        "clf_furnished": clf_furnished,
        "clf_empty": clf_empty,
        "class_names_furnished": {"0": "bathroom", "1": "bedroom", "2": "kitchen"},
        "class_names_empty": {"0": "bathroom", "1": "bedroom", "2": "kitchen"},
        "class_names_occupancy": {"0": "empty", "1": "furnished"},
    })

    dummy_cls = np.zeros((2, 768), dtype=np.float32)
    dummy_patches = np.zeros((2, 256, 768), dtype=np.float32)

    # _classify_and_group does `from group_instances import ...` lazily inside
    # the function body.  We inject a mock module into sys.modules so the import
    # resolves without loading torch/transformers.
    import sys
    mock_gi = MagicMock()
    mock_gi.extract_features.return_value = (dummy_cls, dummy_patches)
    # find_connected_components must return a list of components (each is a list
    # of local indices).  With 1 photo per bucket, each bucket has 1 node → 1
    # singleton component.
    mock_gi.find_connected_components.side_effect = lambda n, edges: [[i] for i in range(n)]
    mock_gi.count_mutual_best_matches.return_value = 0

    with patch.dict(sys.modules, {"group_instances": mock_gi}):
        result = _classify_and_group([Path("/tmp/a.jpg"), Path("/tmp/b.jpg")])

    # Photo 0 (pred=0=empty) must have routed to clf_empty
    clf_empty.predict_proba.assert_called_once()
    # Photo 1 (pred=1=furnished) must have routed to clf_furnished
    clf_furnished.predict_proba.assert_called_once()

    assert result["photos"][0]["occupancy"] == "empty"
    assert result["photos"][1]["occupancy"] == "furnished"
    # Each photo is in its own singleton group (CLS vectors are all-zero → similarity=1.0
    # but identical vectors pass threshold, so they may merge; accept either outcome
    # as long as every photo appears in exactly one group)
    all_indices = [idx for g in result["groups"] for idx in g["photo_indices"]]
    assert sorted(all_indices) == [0, 1]
    # Walk-through is no longer computed at classify time (it runs in the pipeline
    # on user-confirmed groups via POST /walkthrough).
    assert "walkthrough" not in result
