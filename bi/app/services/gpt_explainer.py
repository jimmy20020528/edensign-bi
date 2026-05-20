from __future__ import annotations

import json
import os
from typing import Any

import httpx


def _system_prompt() -> str:
    return (
        "You are a real-estate staging decision copilot. "
        "Explain model outputs in clear business English for non-technical users. "
        "Never invent data. Use only fields provided in the input JSON. "
        "Return valid JSON only."
    )


def _trim_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    """Strip fields the explainer doesn't need to keep the prompt small."""
    trimmed = {k: v for k, v in analysis.items() if k != "all_styles"}
    if "market_context" in trimmed and len(str(trimmed["market_context"])) > 400:
        trimmed["market_context"] = str(trimmed["market_context"])[:400] + "…"
    # Keep redfin_market as-is — it's small and gives GPT real DOM/PSF to cite
    return trimmed


def _user_prompt(analysis: dict[str, Any], client_context: dict[str, Any] | None) -> str:
    payload = {
        "analysis": _trim_analysis(analysis),
        "client_context": client_context or {},
        "required_output_schema": {
            "executive_summary": "string",
            "why_top1": ["string"],
            "action_plan": ["string"],
            "risk_notes": ["string"],
            "confidence_readout": "string",
            "market_benchmark": "1 sentence stating the ZIP median days on market and $/sqft if redfin_market is present, e.g. 'This ZIP averages 26 days on market at $433/sqft.' No data source attribution. Leave empty string if no redfin_market.",
            "buyer_profile_insight": "1-2 sentences explaining what the HMDA buyer data reveals about who is buying in this market and why it supports the top style recommendation. Reference actual numbers (age group, income, archetype). Leave empty string if no hmda_buyer_data.",
            "style_specific_tips": [
                {
                    "style": "string",
                    "tip": "string",
                    "watchout": "string",
                }
            ],
        },
        "requirements": [
            "English only.",
            "Reference warnings if present.",
            "If confidence is medium/low, say the result is a starting point and should be validated with local knowledge. Do NOT mention data availability, missing listings, or data gaps.",
            "Give practical, short action steps for staging execution.",
            "If redfin_market is present in the analysis, state the actual DOM and PSF numbers in market_benchmark without attribution.",
            "If hmda_buyer_data is present, explain in buyer_profile_insight how the actual buyer demographics (age, income, archetype) support the top style choice.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


async def explain_analysis_with_openai(
    analysis: dict[str, Any],
    client_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing in environment.")

    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_prompt(analysis, client_context)},
        ],
        "temperature": 0.2,
    }
    # search models don't support response_format or temperature
    if "search" not in model:
        body["response_format"] = {"type": "json_object"}
    else:
        del body["temperature"]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    content = data["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {"raw_text": content}

    return {
        "provider": "openai",
        "model": model,
        "explanation": parsed,
    }
