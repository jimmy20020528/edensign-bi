from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
import app.main as m

def test_listing_write_passes_template_and_returns_why():
    fake = AsyncMock(return_value={
        "style": "Modern", "template": "story", "headline": "h",
        "paragraphs": ["p"], "full_body": "p", "staging_notes": [],
        "why_summary": "because", "why_steps": {"style": "Story"}})
    with patch.object(m, "build_listing_copy", fake):
        c = TestClient(m.app)
        r = c.post("/listing/write", json={
            "style": "Modern", "street_address": "1 A St",
            "template": "story", "home_report": {"rooms": []}})
    assert r.status_code == 200
    assert fake.call_args.kwargs["template"] == "story"
    assert r.json()["why_summary"] == "because"
