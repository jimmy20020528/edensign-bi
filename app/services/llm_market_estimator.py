from __future__ import annotations

"""
LLM Market Estimator — fallback when no local regression data exists for a ZIP.

Flow:
  1. Resolve ZIP → city/state via pgeocode
  2. Fetch real median PSF + DOM from Redfin cache (absolute price anchor)
  3. Call GPT-4o-search-preview via Responses API so it can web-search real
     staging trends for this specific market before ranking styles
  4. Parse style rankings, clamp to safe ranges, compute absolute PSF per style
  5. Build full response shape (same as analyze_zipcode output)

Why web search:
  - Without search, GPT defaults to "Modern #1 everywhere"
    because that dominates training data / design media
  - With search it can find e.g. "Miami buyers prefer Coastal/Tropical",
    "Austin market favors Mid-Century Modern/Farmhouse", etc.
  - Redfin data anchors absolute $/sqft so style premiums are grounded

Model routing:
  - "search" in model name  → OpenAI Responses API  (/v1/responses)
  - anything else           → Chat Completions API   (/v1/chat/completions)
"""

import asyncio
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Optional

import httpx
import pgeocode

from app.services.redfin_market_data import get_zip_market_data
from app.services.hmda_buyer_data import get_buyer_profile

BI_ROOT = Path(__file__).resolve().parent.parent.parent
MODELS_BASELINE = BI_ROOT / "models" / "baseline"

ALL_STYLES = [
    "Transitional", "Modern", "Scandinavian", "Industrial",
    "Mid-Century Modern", "Luxury", "Coastal", "Farmhouse", "Standard",
]

_nomi = pgeocode.Nominatim("us")


def _city_state_for_zip(zipcode: str) -> tuple[str, str]:
    row = _nomi.query_postal_code(zipcode)
    city = str(row.get("place_name", "")).strip() or "Unknown"
    state = str(row.get("state_code", "")).strip() or "US"
    return city, state


def _load_boston_calibration() -> dict[str, Any]:
    """Premium magnitude reference only — used so LLM doesn't hallucinate ±50% premiums."""
    psf_dirs = sorted(MODELS_BASELINE.glob("log_psf_ridge_*"))
    dom_dirs = sorted(MODELS_BASELINE.glob("log_dom_ridge_*"))
    if not psf_dirs or not dom_dirs:
        return {}
    try:
        psf_eval = json.loads((psf_dirs[-1] / "eval.json").read_text())
        dom_eval = json.loads((dom_dirs[-1] / "eval.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    dom_by_style: dict[str, float] = {}
    for r in dom_eval.get("style_lift_ranking", []):
        name = r["feature"].replace("style_", "").replace("_", " ")
        dom_by_style[name] = float(r["coef"])

    styles = []
    for r in psf_eval.get("style_lift_ranking", []):
        raw_name = r["feature"].replace("style_", "").replace("_", " ")
        coef = float(r["coef"])
        premium_pct = round((math.exp(coef) - 1) * 100, 1)
        dom_coef = dom_by_style.get(raw_name, 0.0)
        styles.append({
            "style": raw_name,
            "psf_premium_pct": premium_pct,
            "dom_adjustment_pct": round((math.exp(dom_coef) - 1) * 100, 1),
        })

    styles.sort(key=lambda x: x["psf_premium_pct"], reverse=True)
    return {
        "note": "Boston regression — use ONLY as magnitude calibration, NOT as ranking template",
        "n_listings": psf_eval.get("n_samples", 0),
        "styles": styles[:10],
    }


def _build_prompt(
    zipcode: str,
    city: str,
    state: str,
    objective: str,
    calibration: dict[str, Any],
    redfin_data: Optional[dict],
    use_search: bool,
    buyer_data: Optional[dict] = None,
) -> str:
    # Buyer archetype phrase for task description
    archetype_phrase = (
        f" The dominant buyer generation is '{buyer_data.get('buyer_archetype', 'Mixed')}' "
        f"(NAR generational label; median income ${buyer_data.get('median_income_k', '?')}k, "
        f"median loan ${buyer_data.get('median_loan_k', '?')}k, "
        f"{buyer_data.get('pct_age_under_45', '?')}% of buyers under 45) — rank styles accordingly."
        if buyer_data else ""
    )

    search_instruction = (
        "IMPORTANT: Before answering, use your web search tool to find EMPIRICAL SOLD DATA. Search for:\n"
        f'  1. "{city} {state} home staging sold data days on market by style 2024"\n'
        f'  2. "{city} real estate staging ROI data sale price interior design style"\n'
        f'  3. "{city} {state} sold listings farmhouse coastal modern minimalist days on market statistics"\n'
        "You are looking for REAL NUMBERS from sold listings — days on market, price per sqft, "
        "sale-to-list ratio broken down by staging/interior style. "
        "Ignore generic 'what styles are popular' articles. Prioritize data from Redfin, Zillow, "
        "local MLS reports, real estate agent market analyses, or staging ROI studies. "
        "If you find actual data, use it. If you cannot find style-specific sold data for this market, "
        "say so in search_sources and fall back to regional market knowledge.\n\n"
        if use_search else ""
    )

    payload: dict[str, Any] = {
        "task": (
            f"{search_instruction}"
            f"Estimate home staging style performance for ZIP {zipcode} ({city}, {state}). "
            f"Rank ALL {len(ALL_STYLES)} styles by expected PREMIUM % on sale price/sqft vs an unstaged property. "
            f"Rankings MUST reflect the specific buyer demographics and style preferences of {city}, {state} — "
            "do NOT default to a generic 'Modern first' ordering."
            f"{archetype_phrase}"
        ),
        "target_market": {
            "zipcode": zipcode,
            "city": city,
            "state": state,
            "objective": objective,
        },
        "premium_magnitude_calibration": calibration,
        "styles_to_rank": ALL_STYLES,
        "output_constraints": {
            "psf_premium_pct_range": [-8, 15],
            "typical_dom_days_range": [7, 120],
            "dom_calibration_note": (
                f"CRITICAL: Redfin data shows this ZIP's median DOM is {redfin_data['median_dom']:.0f} days. "
                f"Your typical_dom_days values MUST be calibrated around {redfin_data['median_dom']:.0f} days — "
                f"best styles roughly {max(3, redfin_data['median_dom']*0.6):.0f}–{redfin_data['median_dom']:.0f} days, "
                f"weakest styles up to {min(120, redfin_data['median_dom']*1.8):.0f} days. "
                "Do NOT use generic 30/60/90 day buckets."
            ) if redfin_data and redfin_data.get("median_dom") and redfin_data["median_dom"] > 0 else (
                "Calibrate typical_dom_days to reflect this specific market's velocity."
            ),
            "diversity_requirement": (
                f"The top-3 styles MUST reflect {city}'s specific market character. "
                "If this is a coastal/tropical market, Coastal/Tropical/Mediterranean should rank higher. "
                "If suburban family market, Farmhouse/Transitional should rank higher. "
                "Only rank Modern or Scandinavian #1 if research confirms it fits THIS market."
            ),
            "psf_premium_note": (
                "psf_premium_pct = % premium vs unstaged baseline. "
                "DO NOT return absolute $/sqft — return only the premium %."
            ),
        },
        "required_output_schema": {
            "market_context": "2-3 sentences summarizing what you found about staging in this market",
            "search_sources": "brief note on what you searched/found (or 'no search' if unavailable)",
            "styles": [
                {
                    "style": "exact name from styles_to_rank",
                    "psf_premium_pct": "float — % premium vs unstaged",
                    "typical_dom_days": "int — days on market for this style in this market",
                    "fit": "high | medium | low — fit for local buyer profile",
                }
            ],
        },
    }

    if redfin_data is not None:
        payload["redfin_market_data"] = {
            "source": "Redfin market tracker (sold listings, All Residential)",
            "median_sale_ppsf": redfin_data["median_psf"],
            "median_dom": redfin_data["median_dom"],
            "homes_sold_last_period": redfin_data["homes_sold"],
            "note": "Use median_sale_ppsf as absolute PSF base. Use median_dom as the DOM anchor — your style-specific typical_dom_days MUST be calibrated around this number.",
        }

    if buyer_data is not None:
        payload["hmda_buyer_data"] = {
            "source": "HMDA 2023 — actual home purchase mortgage records",
            "n_purchases": buyer_data["n_purchases"],
            "median_buyer_income_k": buyer_data["median_income_k"],
            "median_loan_k": buyer_data["median_loan_k"],
            "dominant_age_group": buyer_data["dominant_age_group"],
            "pct_buyers_under_45": buyer_data["pct_age_under_45"],
            "buyer_archetype": buyer_data["buyer_archetype"],
            "note": "Use this to tailor style recommendations to the ACTUAL buyer pool in this market",
        }

    return json.dumps(payload, ensure_ascii=False)


def _weights_for_objective(objective: str) -> dict[str, float]:
    table = {
        "balanced": {"speed": 0.45, "price": 0.45, "support": 0.10},
        "fast":     {"speed": 0.70, "price": 0.20, "support": 0.10},
        "price":    {"speed": 0.20, "price": 0.70, "support": 0.10},
    }
    return table.get(objective, table["balanced"])


def _norm(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.5
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _extract_json(text: str) -> dict:
    """
    Parse JSON from response text that may contain surrounding prose or citations.
    Uses brace-counting so trailing citation arrays like [{"url":"..."}] don't
    confuse rfind('}') into returning a partial/invalid range.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = None
    depth = 0
    in_str = False
    escaped = False
    for i, ch in enumerate(text):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_str:
            escaped = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            if depth == 0:
                start = i  # beginning of a new candidate object
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    start = None  # this block was malformed; look for next {

    raise ValueError(f"No valid JSON object found: {text[:200]}")


async def _call_llm(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    """
    Route to Responses API (web search) for *-search-* models,
    or Chat Completions API for everything else.
    Returns raw text content.
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # gpt-4o-search-preview uses chat/completions but doesn't support
    # response_format or temperature — it auto-searches internally
    if "search" in model:
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=body)
            resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    else:
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=body)
            resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def _build_response(
    zipcode: str,
    city: str,
    state: str,
    objective: str,
    scoring_mode: str,
    llm_styles: list[dict[str, Any]],
    market_context: str,
    redfin_data: Optional[dict],
    buyer_data: Optional[dict] = None,
) -> dict[str, Any]:
    weights = _weights_for_objective(objective)

    premiums = [s["psf_premium_pct"] for s in llm_styles]
    doms = [s["typical_dom_days"] for s in llm_styles]
    pr_min, pr_max = min(premiums), max(premiums)

    if redfin_data and redfin_data.get("median_dom") and redfin_data["median_dom"] > 0:
        redfin_dom = redfin_data["median_dom"]
        dom_min = min(min(doms), redfin_dom * 0.5)
        dom_max = max(max(doms), redfin_dom * 1.5)
    else:
        dom_min, dom_max = min(doms), max(doms)

    items: list[dict[str, Any]] = []
    for s in llm_styles:
        price_comp = _norm(s["psf_premium_pct"], pr_min, pr_max)
        speed_comp = 1.0 - _norm(s["typical_dom_days"], dom_min, dom_max)
        support_comp = 0.5
        score = (
            weights["speed"]   * speed_comp +
            weights["price"]   * price_comp +
            weights["support"] * support_comp
        )
        abs_psf = (
            round(redfin_data["median_psf"] * (1 + s["psf_premium_pct"] / 100), 2)
            if redfin_data is not None else None
        )
        items.append({
            "style": s["style"],
            "n_listings": 0,
            "median_days_on_market": float(s["typical_dom_days"]),
            "median_price_per_sqft": abs_psf,
            "estimated_psf_premium_pct": round(float(s["psf_premium_pct"]), 1),
            "style_score": round(score, 4),
            "confidence": {"score": round(price_comp * 0.6 + speed_comp * 0.4, 4), "warnings": []},
            "market_fit": s.get("fit", "medium"),
            "explain": {
                "speed_component":   round(speed_comp, 4),
                "price_component":   round(price_comp, 4),
                "support_component": round(support_comp, 4),
            },
        })

    items.sort(key=lambda x: x["style_score"], reverse=True)

    redfin_anchored = redfin_data is not None
    notes = [
        f"No sold listings found for {zipcode} in local database.",
        f"Style rankings grounded in GPT-4o web search for {city}, {state} staging market.",
        "Premiums relative to unstaged/vacant property baseline.",
        "Collect local listing data to upgrade to regression-based predictions.",
    ]
    if redfin_anchored:
        notes.insert(0, (
            f"Absolute PSF anchored to Redfin median ${redfin_data['median_psf']:.2f}/sqft "
            f"for ZIP {zipcode} (All Residential, most recent period)."
        ))

    return {
        "zipcode": zipcode,
        "city": city,
        "state": state,
        "objective": objective,
        "weights": weights,
        "scoring_mode": scoring_mode,
        "status": "ok",
        "data_source": "llm_estimate",
        "redfin_anchored": redfin_anchored,
        "redfin_market": {
            "median_psf": redfin_data["median_psf"],
            "median_dom": redfin_data["median_dom"],
            "homes_sold": redfin_data["homes_sold"],
        } if redfin_data else None,
        "hmda_buyer_data": buyer_data,
        "warnings": [],
        "recommended_styles": items[:3],
        "all_styles": items,
        "confidence": {
            "overall": "medium",
            "n_listings": 0,
            "style_count": len(items),
        },
        "market_context": market_context,
        "methodology": {
            "objective": "LLM-estimated style rankings with web search — no local sold data for this ZIP.",
            "notes": notes,
        },
    }


async def estimate_market_for_zip(
    zipcode: str,
    objective: str = "balanced",
    scoring_mode: str = "hybrid",
) -> dict[str, Any]:
    """Main entry point. Returns same shape as analyze_zipcode()."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY missing — cannot run LLM fallback.")

    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL_ESTIMATOR", "gpt-4o-search-preview")
    use_search = "search" in model

    city, state = _city_state_for_zip(zipcode)
    calibration = _load_boston_calibration()
    # Run blocking I/O in a thread pool to avoid stalling the event loop
    redfin_data, buyer_data = await asyncio.gather(
        asyncio.to_thread(get_zip_market_data, zipcode),
        asyncio.to_thread(get_buyer_profile, zipcode),
    )
    user_prompt = _build_prompt(
        zipcode, city, state, objective, calibration, redfin_data, use_search,
        buyer_data=buyer_data,
    )

    system_prompt = (
        "You are a real estate data analyst specializing in home staging ROI. "
        + (
            "Use your web search tool to find SOLD LISTING DATA — actual days on market, "
            "sale price per sqft, or sale-to-list ratios broken down by staging/interior style "
            "for the target market. Do NOT rely on generic 'staging tips' or 'popular styles' articles. "
            "Only rank styles based on empirical performance data you find. "
            "If no style-specific sold data exists for this market, state that clearly in search_sources "
            "and use regional comparables. "
            if use_search else
            "Use your knowledge of US housing market data to answer. "
        ) +
        "Return ONLY a valid JSON object matching the required_output_schema. "
        "No markdown, no prose outside the JSON."
    )

    raw = await _call_llm(base_url, api_key, model, system_prompt, user_prompt)
    parsed = _extract_json(raw)

    llm_styles: list[dict[str, Any]] = parsed.get("styles", [])
    market_context: str = parsed.get("market_context", "")

    for s in llm_styles:
        s["psf_premium_pct"] = max(-8.0, min(15.0, float(s.get("psf_premium_pct", 0))))
        s["typical_dom_days"] = max(7, min(120, int(s.get("typical_dom_days", 30))))

    # Rescale DOM to Redfin anchor if LLM median is far off from reality.
    # Preserves relative style ordering while grounding absolute values.
    if redfin_data and redfin_data.get("median_dom") and redfin_data["median_dom"] > 0 and llm_styles:
        redfin_dom = redfin_data["median_dom"]
        doms = sorted(s["typical_dom_days"] for s in llm_styles)
        llm_median_dom = doms[len(doms) // 2]
        # Only rescale if LLM is off by more than 40% from Redfin
        if llm_median_dom > 0 and abs(llm_median_dom - redfin_dom) / redfin_dom > 0.4:
            scale = redfin_dom / llm_median_dom
            dom_floor = max(3, round(redfin_dom * 0.7))  # fastest style ≥ 70% of market median
            for s in llm_styles:
                s["typical_dom_days"] = max(dom_floor, round(s["typical_dom_days"] * scale))

    known = set(ALL_STYLES)
    llm_styles = [s for s in llm_styles if s.get("style") in known]

    if not llm_styles:
        raise RuntimeError(f"LLM returned no recognizable styles for {zipcode}")

    return _build_response(
        zipcode, city, state, objective, scoring_mode,
        llm_styles, market_context, redfin_data,
        buyer_data=buyer_data,
    )
