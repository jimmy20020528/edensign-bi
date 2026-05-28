from __future__ import annotations

import logging
import os

import httpx

from src.models.schemas import (
    ConditionRating,
    FinalReport,
    QualityRating,
    RoomSummary,
    UpgradeAction,
)
from src.vlm.prompts import REPORT_POLISH_PROMPT

logger = logging.getLogger(__name__)

_QUALITY_LABEL = {
    "Q1": "exceptional architect-designed quality",
    "Q2": "high-end custom quality",
    "Q3": "above-builder-grade quality",
    "Q4": "standard builder-grade quality",
    "Q5": "economy-grade quality",
    "Q6": "below-minimum quality",
}
_CONDITION_LABEL = {
    "C1": "new construction",
    "C2": "like-new condition",
    "C3": "good, well-maintained condition",
    "C4": "fair condition with minor deferred maintenance",
    "C5": "poor condition with obvious deterioration",
    "C6": "critical condition with significant damage",
}

_TEMPLATE = """\
Property Assessment Summary

Overall Quality Rating:    {q_rating} — {q_label} (score {q_decimal:.1f} / 6.0)
Overall Condition Rating:  {c_rating} — {c_label} (score {c_decimal:.1f} / 6.0)

Room-by-Room Assessment:
{rooms_text}
Assessment based on {image_count} photo(s) across {room_count} area(s): {room_list}.

Upgrade Action Summary:
- {must_do_count} urgent maintenance item(s) requiring immediate attention
- {recommended_count} recommended upgrade(s) with strong ROI
- {optional_count} optional enhancement(s)
{coverage}\
"""


def _rooms_text(summaries: list[RoomSummary]) -> str:
    lines = []
    for s in summaries:
        mat_parts = []
        for field in ("countertop", "flooring", "cabinets", "fixtures", "appliances"):
            val = getattr(s.detected_materials, field)
            if val and val != "unknown":
                mat_parts.append(f"{field}: {val.replace('_', ' ')}")
        mat_str = "; ".join(mat_parts) if mat_parts else "materials not identified"
        features = (", ".join(s.notable_features[:3]) + ".") if s.notable_features else ""
        lines.append(
            f"  {s.room_type.value.replace('_', ' ').title()}: "
            f"{s.quality_rating.value} / {s.condition_rating.value} — "
            f"{_QUALITY_LABEL.get(s.quality_rating.value, '')}. "
            f"{s.quality_rationale} "
            f"{('Notable: ' + features) if features else ''}"
        )
    return "\n".join(lines)


async def _polish(text: str) -> str:
    """LLM narrative polish. Falls back to template text if no API key available."""
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return text

    prompt = REPORT_POLISH_PROMPT.format(template_text=text)
    use_openai = bool(os.environ.get("OPENAI_API_KEY")) and not os.environ.get("ANTHROPIC_API_KEY")

    try:
        if use_openai:
            body = {
                "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            }
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post("https://api.openai.com/v1/chat/completions",
                                         headers=headers, json=body)
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
        else:
            body = {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            }
            headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01",
                       "content-type": "application/json"}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post("https://api.anthropic.com/v1/messages",
                                         headers=headers, json=body)
                resp.raise_for_status()
                return resp.json()["content"][0]["text"].strip()
    except Exception as exc:
        logger.warning("Narrative polish failed, using template: %s", exc)
        return text


async def build_report(
    image_count: int,
    summaries: list[RoomSummary],
    prioritized: list[UpgradeAction],
    overall_q: float,
    overall_c: float,
    coverage: str | None,
) -> FinalReport:
    must_do = [a for a in prioritized if a.priority_bucket == "must_do"]
    recommended = [a for a in prioritized if a.priority_bucket == "recommended"]
    optional = [a for a in prioritized if a.priority_bucket == "optional"]

    q_rating = QualityRating(f"Q{max(1, min(6, int(overall_q)))}")
    c_rating = ConditionRating(f"C{max(1, min(6, int(overall_c)))}")
    room_list = ", ".join(s.room_type.value.replace("_", " ") for s in summaries) or "none"

    template_text = _TEMPLATE.format(
        q_rating=q_rating.value,
        q_label=_QUALITY_LABEL.get(q_rating.value, ""),
        q_decimal=overall_q,
        c_rating=c_rating.value,
        c_label=_CONDITION_LABEL.get(c_rating.value, ""),
        c_decimal=overall_c,
        rooms_text=_rooms_text(summaries),
        image_count=image_count,
        room_count=len(summaries),
        room_list=room_list,
        must_do_count=len(must_do),
        recommended_count=len(recommended),
        optional_count=len(optional),
        coverage=f"\nNote: {coverage}\n" if coverage else "",
    )

    narrative = await _polish(template_text)

    return FinalReport(
        overall_quality_rating=q_rating,
        overall_quality_decimal=overall_q,
        overall_condition_rating=c_rating,
        overall_condition_decimal=overall_c,
        overall_narrative=narrative,
        rooms=summaries,
        must_do=must_do,
        recommended=recommended,
        optional=optional,
        stats={
            "image_count": image_count,
            "room_count": len(summaries),
            "overall_quality": q_rating.value,
            "overall_quality_decimal": overall_q,
            "overall_condition": c_rating.value,
            "overall_condition_decimal": overall_c,
            "must_do_count": len(must_do),
            "recommended_count": len(recommended),
            "optional_count": len(optional),
        },
        coverage_note=coverage,
    )
