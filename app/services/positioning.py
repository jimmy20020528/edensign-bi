from __future__ import annotations

"""
Buyer Appeal — the demo report's short "who buys this and why" narrative
(e.g. "This listing targets buyers looking for a high-utility luxury home by
emphasizing the … workshop and four-car garage…").

Grounded, single text-LLM call (same discipline as gpt_explainer / neighborhood):
it may only use the property's actual standout features, specs, and market context
that are passed in — never invent a feature or amenity.

(Market Positioning is intentionally NOT here — it lives in the CMA narrative,
`redfin_comps.generate_comps_narrative_openai`.)
"""

import json
import os
from typing import Any, Optional

import httpx

from app.services.listing_writer import _scrub_cliches  # shared cliché phrase-scrub


def _collect_features(home_report: Optional[dict]) -> list[str]:
    """Dedup the notable features the home report saw across rooms."""
    if not isinstance(home_report, dict):
        return []
    feats: list[str] = []
    for r in (home_report.get("rooms") or []):
        for f in (r.get("notable_features") or []):
            label = str(f).replace("_", " ").strip()
            if label and label not in feats:
                feats.append(label)
    return feats[:25]


def _system_prompt() -> str:
    return (
        "You write the 'Buyer Appeal' paragraph of a real-estate listing review: "
        "who the ideal buyer is and which concrete features drive the most interest. "
        "Use ONLY the standout_features, specs, and market context in the provided "
        "JSON — never invent a feature, amenity, room, or number. Do NOT cite a walk "
        "score, transit score, or any location statistic (walkability is covered "
        "elsewhere). Do NOT add exterior or landscaping details (ivy, gardens, views) "
        "that are not in standout_features. 2-4 sentences, warm and concrete. "
        "Avoid clichés — never use: charming, stunning, gorgeous, dream, nestled, "
        "boasting, spacious, inviting, elegant, perfect for, ideal for, gem, vibrant, "
        "cozy, retreat, must-see, walker's paradise, touch of luxury. "
        "Return valid JSON only."
    )


def _trim_market(market: Optional[dict]) -> dict:
    """Drop anything carrying a walk/transit/bike score so Buyer Appeal can't cite
    one (it would conflict with the address-level score in the Neighborhood section).
    Keeps the recommended style + buyer demographics, which are what this section uses."""
    if not isinstance(market, dict):
        return {}
    return {k: v for k, v in market.items()
            if "walk" not in k.lower() and "score" not in k.lower()}


def _user_prompt(home_report: Optional[dict], market: Optional[dict], specs: Optional[dict]) -> str:
    payload = {
        "specs": specs or {},
        "standout_features": _collect_features(home_report),
        "overall_quality": (home_report or {}).get("overall_quality_rating"),
        "overall_condition": (home_report or {}).get("overall_condition_rating"),
        "market_context": _trim_market(market),
        "required_output_schema": {
            "buyer_appeal": "2-4 sentences: the target buyer + the specific features "
                            "(from standout_features/specs) that drive interest. Only real features.",
        },
        "rules": [
            "English only.",
            "Every feature you mention MUST be in standout_features or specs.",
            "Do NOT cite a walk/transit/bike score or any number not in specs.",
            "Do NOT invent exterior or landscaping details (ivy, gardens, views, plants) "
            "that are not in standout_features.",
            "No invented rooms, amenities, or statistics. No data-source attribution.",
            "If standout_features is sparse, keep it short and factual.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


async def generate_buyer_appeal_openai(
    home_report: Optional[dict] = None,
    market: Optional[dict] = None,
    specs: Optional[dict] = None,
) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is missing in environment.")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_prompt(home_report, market, specs)},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(base_url + "/chat/completions", headers=headers, json=body)
        resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    appeal = _scrub_cliches(json.loads(content).get("buyer_appeal", ""))
    return {"provider": "openai", "model": model, "buyer_appeal": appeal}
