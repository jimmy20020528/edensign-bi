from __future__ import annotations

"""
Listing Writer — GPT-4o-mini powered listing copy generator.

Standalone component. No connection to ZIP analysis or style atlas.
Input:  style, street_address, property info, agent info, optional requirements
Output: headline, paragraphs, staging_notes
"""

import json
import logging
import os
from typing import Any, Optional

import httpx

from app.services.listing_templates import get_template

logger = logging.getLogger(__name__)


# Retained (currently unused): the original "spare voice". Kept pending George's decision
# on whether to offer it as a 6th "Minimal" template. Do not delete without that decision.
def _system_prompt(has_images: bool = False) -> str:
    base = """You are a real estate copywriter known for spare, precise language that sells without hype.

HARD RULES:
- No em dashes (—) or hyphens used as sentence connectors. Use commas or periods.
- No buyer language. Never say who the buyer is or who this is "perfect for."
- No clichés. Forbidden words: stunning, gorgeous, dream, nestled, boasting, spacious, inviting, elegant, amazing, incredible, awaits, discover, unique blend, authentic character, natural light floods, perfect for, ideal for, don't miss, rare find, gem, vibrant, renowned, pivotal, testament, landmark.
- No filler sentences. Every sentence must say something specific.
- No AI-sounding phrases. Forbidden: "invites creativity", "harmonize", "elevate", "invigorated", "seamlessly", "thoughtfully designed", "meticulously crafted", "serves as", "showcases", "highlights", "interplay", "landscape."
- No meta-language. NEVER tell the reader to imagine, picture, or envision anything. Forbidden: "imagine", "picture", "envision", "see yourself", "find yourself", "invites you to", "transport you", "lets you", "allows you to."
- No filler transitions. Forbidden: "additionally", "furthermore", "moreover", "in addition", "not only that", "what's more."
- No rule-of-three. Do not list three items in a row for rhetorical effect.
- No data or statistics in the copy. Never quote walk scores, DOM numbers, income figures, or any numeric market data in the listing text.
- Vary sentence length. Mix very short sentences (3-5 words) with medium ones (10-15 words). Monotone rhythm kills good copy.
- Present tense. Active voice.
- No exclamation marks.

GOOD EXAMPLES (study the tone and copy exactly this style):
"47 Brainerd Rd. 850 square feet, two bedrooms, one bath. Warm without being dated. Clean without being sterile."
"Soft tones throughout, grounded by natural materials and clean upholstered lines. Walk out the front door and coffee, groceries, and the T are all within a few minutes."
"Linen drapes, a marble accent, hardware that earns a second look."
"88 Wythe Ave. 720 square feet, loft, raw ceiling. The bones of the space are the point."
"Concrete and steel, left as they are. Cooking here feels like an event."

BAD EXAMPLES (never write anything like these):
"Welcome to this stunning loft where industrial elegance awaits."
"This condo combines warmth with a modern sensibility."
"The layout supports everyday living while offering comfort and style."
"Attention to detail is evident throughout."
"Craftsmanship is evident in every corner."
"The atmosphere is calm and balanced."
"Creates a welcoming environment."
"Enhance everyday living."
"Easy transit access enhances connectivity."
"Raw materials invite a tactile experience."
"Imagine waking up to morning light filtering through linen drapes."
"Picture yourself in the kitchen, where clean lines meet warm wood."
"This home invites you to slow down and appreciate the details."

Return valid JSON only. No markdown, no code fences."""

    if has_images:
        base += "\n\nYou have been provided photos of the staged property. Base your descriptions on what you actually see: specific materials, colors, furniture, fixtures, and proportions visible in the images. Do not invent details that are not visible in the photos."

    return base


def _walkability_label(score: int) -> str:
    if score >= 90: return "walker's paradise — daily errands on foot, no car needed"
    if score >= 70: return "very walkable — most errands reachable on foot"
    if score >= 50: return "somewhat walkable — some errands on foot"
    return "car-dependent"


def _transit_label(score: int) -> str:
    if score >= 75: return "excellent transit — frequent, reliable service nearby"
    if score >= 50: return "good transit — most trips covered by transit"
    if score >= 25: return "some transit nearby"
    return "minimal transit"


def _extract_market_signals(market_data: dict[str, Any]) -> dict[str, Any]:
    """Pull only the signals useful for copy from the full analysis blob."""
    signals: dict[str, Any] = {}

    ws = market_data.get("walk_score_data") or {}
    if ws.get("walk_score") is not None:
        signals["walkability"] = _walkability_label(ws["walk_score"])
    if ws.get("transit_score") is not None:
        signals["transit"] = _transit_label(ws["transit_score"])

    rf = market_data.get("redfin_market") or {}
    if rf.get("median_dom") is not None:
        signals["median_dom_days"] = round(rf["median_dom"])
    if rf.get("median_psf") is not None:
        signals["median_psf"] = rf["median_psf"]

    sc = market_data.get("school_profile") or {}
    if sc.get("quality_score") is not None:
        signals["school_quality_score"] = sc["quality_score"]
        signals["has_elementary"] = sc.get("has_elementary", False)
        signals["has_high_school"] = sc.get("has_high_school", False)

    hm = market_data.get("hmda_buyer_data") or {}
    if hm.get("median_income_k"):
        signals["area_median_income_k"] = hm["median_income_k"]
    if hm.get("dominant_age_group"):
        signals["dominant_buyer_age_group"] = hm["dominant_age_group"]
    if hm.get("buyer_archetype"):
        signals["buyer_generation"] = hm["buyer_archetype"]

    return signals


def _market_instructions(signals: dict[str, Any]) -> list[str]:
    """Translate market signals into copy directives for GPT."""
    instructions: list[str] = []

    walkability = signals.get("walkability", "")
    if "paradise" in walkability:
        instructions.append("The neighborhood is extremely walkable. Weave in the on-foot character naturally: coffee, groceries, transit nearby. No scores or numbers.")
    elif "very walkable" in walkability:
        instructions.append("The neighborhood is very walkable. Briefly mention that daily errands are on foot. No scores or numbers.")
    elif "car-dependent" in walkability:
        instructions.append("The area is car-dependent. Skip walkability entirely. Focus on the home and setting.")

    dom = signals.get("median_dom_days")
    if dom is not None:
        if dom <= 20:
            instructions.append("This market moves fast. Write copy that is confident and decisive. Short sentences. No hedging.")
        elif dom >= 60:
            instructions.append("The market here is patient. Be more descriptive and specific. The copy should give the listing something to stand on.")

    school_q = signals.get("school_quality_score", 0)
    has_elem = signals.get("has_elementary", False)
    if school_q >= 7 and has_elem:
        instructions.append("School quality in this ZIP is strong. Without mentioning buyers or families explicitly, use language that suggests the home accommodates multiple lives well, flexible rooms, a dedicated workspace, or outdoor space.")

    income = signals.get("area_median_income_k", 0)
    if income >= 180:
        instructions.append("This is a high-expectation market. The copy should be precise and material-specific. Every claim must be earned.")
    elif income <= 80:
        instructions.append("The copy should feel honest and practical. No aspirational luxury language.")

    transit = signals.get("transit", "")
    if "excellent" in transit or "good" in transit:
        instructions.append("Transit is strong here. You may briefly reference easy commute access. No scores or numbers.")

    return instructions


def _paragraphs_instruction(has_images: bool) -> str:
    if has_images:
        return (
            "Array of EXACTLY 5 strings. No more, no less. "
            "Total word count: 320-370 words. Each paragraph is 3-5 sentences. "
            "Roughly half the content should describe what is actually visible in the photos: specific materials, colors, furniture, fixtures, proportions. "
            "The other half should focus on staging style character, market signals, and neighborhood context from the provided data. "
            "P1: address, specs, and the immediate visual impression from the photos. "
            "P2: describe specific finishes, surfaces, and palette visible in the photos. Name what you see. "
            "P3: another space or detail from the photos, or a standout fixture or material. If only one room is visible, focus on a specific detail. "
            "P4: how the staging style and market data frame this property, what the area signals about buyers and pace. Do not name buyer demographics. "
            "P5: neighborhood, commute, transit, coffee, parks. Grounded and specific. "
            "Do NOT put contact info in any paragraph."
        )
    else:
        return (
            "Array of EXACTLY 5 strings. No more, no less. "
            "Total word count: 320-370 words. Each paragraph is 3-5 sentences. "
            "IMPORTANT: No photos were provided. Do NOT invent specific finishes, fixtures, or room details you cannot know. "
            "P1: address, specs (beds/baths/sqft), and the immediate character this staging style brings to a space of this type and scale. State facts and style tone directly. "
            "P2: the material language of this staging style: specific surfaces, palette, textures. Name them. Do not say 'imagine' or 'picture'. Just describe what the style looks like. "
            "P3: the layout logic and practical qualities of a property this size and type. Be concrete: room count, flow, what the kitchen or main space typically holds. No invented details. "
            "P4: the area character and market pace, translated into atmosphere. No numbers or statistics. What this neighborhood actually feels like on a Tuesday morning. "
            "P5: neighborhood, commute, transit, coffee, parks. Name specific things if the data supports it. Grounded and short. "
            "Do NOT put contact info in any paragraph."
        )


def _price_tier(listing_price: int) -> str:
    if listing_price < 400_000:
        return "under $400k"
    elif listing_price < 700_000:
        return "$400k–$700k"
    elif listing_price < 1_200_000:
        return "$700k–$1.2m"
    elif listing_price < 2_500_000:
        return "$1.2m–$2.5m"
    else:
        return "above $2.5m"


def _user_prompt(
    style: str,
    street_address: str,
    bedrooms: Optional[int],
    bathrooms: Optional[float],
    sqft: Optional[int],
    property_type: str,
    agent_name: Optional[str],
    agent_contact: Optional[str],
    additional_requirements: Optional[str],
    market_signals: Optional[dict[str, Any]],
    listing_price: Optional[int] = None,
    has_images: bool = False,
    paragraph_instruction: Optional[str] = None,
    visual_detail: Optional[str] = None,
) -> str:
    parts = []
    if bedrooms is not None:
        parts.append(f"{bedrooms} bed")
    if bathrooms is not None:
        bath_str = str(int(bathrooms)) if bathrooms == int(bathrooms) else str(bathrooms)
        parts.append(f"{bath_str} bath")
    parts.append(property_type)
    if sqft is not None:
        parts.append(f"{sqft:,} sqft")
    parts.append(f"at {street_address}")
    prop_summary = ", ".join(parts[:-1]) + " " + parts[-1] if len(parts) > 1 else parts[0]

    payload: dict[str, Any] = {
        "task": "Write a listing description for a staged property.",
        "staging_style": style,
        "style_context": _style_context(style),
        "property": prop_summary,
        "agent_name": agent_name,
        "agent_contact": agent_contact,
        "additional_requirements": additional_requirements or None,
        "required_output_fields": {
            "headline": "Short title: '{beds}BR {type} in {city}, {style tagline}'. Comma not dash. Max 12 words.",
            "paragraphs": ("Return a JSON array of strings, one element per paragraph. " + paragraph_instruction) if paragraph_instruction else _paragraphs_instruction(has_images),
            "staging_notes": "Array of 5 staging directives for the team. One sentence each. Specific and actionable.",
            "why_summary": "One natural sentence assessing WHY this listing reads as it does, given the property, market, photos, and style. An assessment, not a recap. No numbers or scores.",
            "why_steps": "Object with keys among your_info, market, from_photos, style — each a short grounded phrase. OMIT a key entirely if there is no real signal for it (e.g., no photos, or market is just an estimate).",
        },
    }

    if visual_detail:
        payload["visual_detail"] = visual_detail

    if listing_price:
        payload["listing_price_tier"] = _price_tier(listing_price)
        payload["price_context_instruction"] = (
            f"This property is listed in the {_price_tier(listing_price)} price range. "
            "Only mention an upgrade or feature if it would be genuinely notable at this price point. "
            "At lower price points, things like quartz counters, stainless appliances, or in-unit laundry are worth calling out. "
            "At higher price points, only call out features that are truly exceptional: custom millwork, professional-grade appliances, rare materials. "
            "Skip anything that is standard for the price tier."
        )

    if market_signals:
        payload["market_data"] = market_signals
        market_instr = _market_instructions(market_signals)
        if market_instr:
            payload["market_instructions"] = market_instr

    return json.dumps(payload, ensure_ascii=False)


def _style_context(style: str) -> str:
    contexts = {
        "Transitional": "Warm neutrals, mixed wood and metal, neither modern nor traditional. Clean upholstered lines. Oak tones. Brushed hardware.",
        "Modern": "High contrast, strong silhouettes, matte surfaces. Restrained palette. Every element has a reason to be there.",
        "Scandinavian": "Pale oak, off-white walls, woven textiles. Warm and functional. Hygge without being precious.",
        "Industrial": "Exposed concrete, blackened steel, reclaimed wood. Raw materials are the palette. High ceilings if present.",
        "Mid-Century Modern": "Warm walnut or teak, tapered legs, organic curves, and flat planes. Wool upholstery, amber tones. Nothing cold.",
        "Luxury": "Bespoke materials, curated art, exceptional hardware. Every surface is deliberate. The quality is visible before anyone says a word.",
        "Coastal": "Bleached linen, sea glass tones, natural textures. Light and airy. Makes you feel like you stopped rushing.",
        "Farmhouse": "Shiplap whites, worn wood, apron sink, cotton textiles. Substantial and warm. The kitchen is the center.",
        "Standard": "Clean, neutral, unadorned. Clear sightlines, consistent palette. The home is the product, not the décor.",
    }
    return contexts.get(style, f"{style} staging style.")


def _extract_visual_detail(home_report) -> Optional[str]:
    """Turn home-report-ai's per-room VLM output into specificity bullets. No new VLM call."""
    if not isinstance(home_report, dict):
        return None
    lines = []
    for room in home_report.get("rooms", []):
        if not isinstance(room, dict):
            continue
        rt = room.get("room_type", "room")
        mats = room.get("detected_materials")
        if not isinstance(mats, dict):
            mats = {}
        mat_str = ", ".join(f"{k}: {v}" for k, v in mats.items() if v)
        raw_feats = room.get("notable_features")
        feats = ", ".join(raw_feats) if isinstance(raw_feats, list) else ""
        bits = " | ".join(b for b in (mat_str, feats) if b)
        if bits:
            lines.append(f"- {rt}: {bits}")
    if not lines:
        return None
    return "Visible details from the photos (use these specific materials/features):\n" + "\n".join(lines)


async def build_listing_copy(
    style: str,
    street_address: str,
    bedrooms: Optional[int] = None,
    bathrooms: Optional[float] = None,
    sqft: Optional[int] = None,
    property_type: str = "residential",
    agent_name: Optional[str] = None,
    agent_contact: Optional[str] = None,
    listing_price: Optional[int] = None,
    additional_requirements: Optional[str] = None,
    market_data: Optional[dict[str, Any]] = None,
    images: Optional[list[str]] = None,  # base64 data URLs: "data:image/jpeg;base64,..."
    template: str = "word_optimized",
    home_report: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing in environment.")

    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_LISTING_MODEL", "gpt-4o")

    market_signals = _extract_market_signals(market_data) if market_data else None
    has_images = bool(images)
    logger.info("listing_writer: has_images=%s n_images=%d model=%s", has_images, len(images) if images else 0, model)

    tmpl = get_template(template)
    visual_detail = _extract_visual_detail(home_report)

    user_text = _user_prompt(
        style, street_address, bedrooms, bathrooms, sqft,
        property_type, agent_name, agent_contact,
        additional_requirements, market_signals,
        listing_price=listing_price,
        has_images=has_images,
        paragraph_instruction=tmpl["paragraph_instruction"],
        visual_detail=visual_detail,
    )

    system_content = tmpl["system_prompt"]
    if has_images:
        system_content += "\n\nYou have been provided photos of the staged property. Base your descriptions on what you actually see: specific materials, colors, furniture, fixtures, and proportions visible in the images. Do not invent details that are not visible in the photos."

    if has_images:
        user_content: Any = [{"type": "text", "text": user_text}]
        for img_url in images:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": img_url, "detail": "high"},
            })
    else:
        user_content = user_text

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user",   "content": user_content},
        ],
        "temperature": 0.6,
        "response_format": {"type": "json_object"},
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    content = data["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {}

    headline      = parsed.get("headline", f"{bedrooms}BR {property_type} in {street_address.split(',')[1].strip() if ',' in street_address else ''}, {style}")
    paragraphs    = parsed.get("paragraphs", [])
    # The LLM sometimes returns `paragraphs` as a single string (esp. shorter templates).
    # Coerce to a list of strings so the cleanup below never iterates it character-by-character.
    if isinstance(paragraphs, str):
        paragraphs = [paragraphs]
    elif not isinstance(paragraphs, list):
        paragraphs = []
    paragraphs = [p for p in paragraphs if isinstance(p, str) and p.strip()]
    staging_notes = parsed.get("staging_notes", [])
    logger.info("listing_writer: GPT returned %d paragraphs", len(paragraphs))

    clean_paras = [p for p in paragraphs
                   if not (agent_contact and agent_contact in p)
                   and not (agent_name and agent_name in p)]
    if agent_name and agent_contact:
        all_paragraphs = clean_paras[:5] + [f"Contact {agent_name} at {agent_contact}."]
    else:
        all_paragraphs = clean_paras[:5]

    return {
        "style":         style,
        "template":      template,
        "headline":      headline,
        "paragraphs":    all_paragraphs,
        "full_body":     "\n\n".join(all_paragraphs),
        "staging_notes": staging_notes,
        "why_summary":   parsed.get("why_summary", ""),
        "why_steps":     parsed.get("why_steps", {}),
    }
