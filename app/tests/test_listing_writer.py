# app/tests/test_listing_writer.py
import json
from unittest.mock import AsyncMock, patch
import pytest
from app.services import listing_writer

HOME_REPORT = {"rooms": [
    {"room_type": "kitchen", "quality_decimal": 5.0,
     "detected_materials": {"countertop": "quartz", "flooring": "white oak", "appliances": "stainless"},
     "notable_features": ["island", "pendant lighting"]},
]}

def _mock_openai(content: dict):
    resp = AsyncMock()
    resp.raise_for_status = lambda: None
    resp.json = lambda: {"choices": [{"message": {"content": json.dumps(content)}}]}
    return resp

@pytest.mark.asyncio
async def test_template_selects_system_prompt(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    captured = {}
    async def fake_post(self, url, headers=None, json=None):
        captured["body"] = json
        return _mock_openai({"headline": "h", "paragraphs": ["p"], "staging_notes": [],
                             "why_summary": "ws", "why_steps": {"style": "Story"}})
    with patch.object(listing_writer.httpx.AsyncClient, "post", new=fake_post):
        out = await listing_writer.build_listing_copy(
            style="Modern", street_address="1 A St", template="story",
            home_report=HOME_REPORT)
    sys_prompt = captured["body"]["messages"][0]["content"]
    assert "narrative" in sys_prompt.lower()         # story template's system prompt
    assert out["why_summary"] == "ws"
    assert out["template"] == "story"
    assert out["why_steps"] == {"style": "Story"}

@pytest.mark.asyncio
async def test_home_report_visual_injected(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    captured = {}
    async def fake_post(self, url, headers=None, json=None):
        captured["body"] = json
        return _mock_openai({"headline": "h", "paragraphs": ["p"], "staging_notes": [],
                             "why_summary": "ws", "why_steps": {}})
    with patch.object(listing_writer.httpx.AsyncClient, "post", new=fake_post):
        await listing_writer.build_listing_copy(
            style="Modern", street_address="1 A St", template="concise",
            home_report=HOME_REPORT)
    user_msg = captured["body"]["messages"][1]["content"]
    assert "quartz" in user_msg and "white oak" in user_msg   # visual detail reached the prompt


@pytest.mark.asyncio
async def test_paragraphs_returned_as_string_not_char_exploded(monkeypatch):
    # Regression: when the LLM returns `paragraphs` as a single string (not a list),
    # build_listing_copy must NOT iterate it character-by-character (which produced
    # full_body == "S\n\nt\n\ne\n\np" in the UI).
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    async def fake_post(self, url, headers=None, json=None):
        return _mock_openai({"headline": "h",
                             "paragraphs": "Step into the bright kitchen.",
                             "staging_notes": [], "why_summary": "", "why_steps": {}})
    with patch.object(listing_writer.httpx.AsyncClient, "post", new=fake_post):
        out = await listing_writer.build_listing_copy(
            style="Modern", street_address="1 A St", template="concise")
    assert out["paragraphs"] == ["Step into the bright kitchen."]
    assert out["full_body"] == "Step into the bright kitchen."


@pytest.mark.asyncio
async def test_prompt_names_chosen_template_and_has_accuracy_guard(monkeypatch):
    # The `style` why-step must reflect the chosen WRITING template (named in the prompt),
    # and the prompt must carry an anti-hallucination guard so rooms not in the photos
    # are not described.
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    captured = {}
    async def fake_post(self, url, headers=None, json=None):
        captured["body"] = json
        return _mock_openai({"headline": "h", "paragraphs": ["p"], "staging_notes": [],
                             "why_summary": "", "why_steps": {}})
    with patch.object(listing_writer.httpx.AsyncClient, "post", new=fake_post):
        await listing_writer.build_listing_copy(
            style="Modern", street_address="1 A St", template="story")
    user_msg = captured["body"]["messages"][1]["content"]
    assert "chosen_writing_template" in user_msg and "Story" in user_msg  # writing template named for `style` step
    assert "accuracy_rules" in user_msg                                   # anti-hallucination guard present
    body = json.loads(user_msg)
    assert "facts_rule" in body                                           # never-invent-counts guard present
    assert "bed" not in body["property"].lower()                          # no bedroom count was provided -> not stated
