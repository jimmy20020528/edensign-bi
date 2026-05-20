from __future__ import annotations

"""
Listing Prescription — rule-based staging + copy guidance.

Generates a human-readable listing description from pre-computed analysis data.
No LLM. All copy is hand-written; data signals choose which blocks to combine.
"""

import random
from typing import Any, Optional


# ── sentence pools ──────────────────────────────────────────────────────────
# Each pool has multiple variants so repeated calls don't feel identical.
# Keys are the condition labels used in _build_blocks().

_POOL: dict[str, list[str]] = {
    # walkability
    "walk_paradise": [
        "Everything is within reach on foot.",
        "Walk to everything — coffee, groceries, restaurants, transit.",
        "A true walker's neighborhood: no car needed for daily life.",
    ],
    "walk_very": [
        "Most errands are a short walk away.",
        "Daily essentials are all walkable from the front door.",
        "Walkable neighborhood with shops and dining close by.",
    ],
    "walk_somewhat": [
        "Some destinations are walkable; a car comes in handy for the rest.",
        "A mix of walkable conveniences and easy driving access.",
    ],
    "walk_car": [
        "Car-dependent area — buyers who drive will feel right at home.",
        "Quiet residential setting best suited for buyers with a vehicle.",
    ],

    # transit
    "transit_good": [
        "Strong public transit options keep commutes simple.",
        "Bus and rail connections make car-free living realistic.",
    ],
    "transit_some": [
        "Some transit options available nearby.",
    ],

    # schools
    "school_strong": [
        "Served by well-regarded local schools.",
        "Top-rated schools are a key draw for families in this area.",
        "Strong school coverage makes this a natural fit for family buyers.",
    ],
    "school_some": [
        "Local schools nearby.",
        "Elementary school access within the neighborhood.",
    ],

    # market speed — fast
    "market_fast": [
        "This market moves quickly — well-priced homes don't sit.",
        "Homes here sell fast; buyers come prepared.",
        "Low days-on-market signals a competitive, active market.",
    ],
    "market_moderate": [
        "A steady market with healthy buyer activity.",
        "Consistent demand keeps this market moving at a measured pace.",
    ],
    "market_slow": [
        "A patient market where presentation and pricing strategy matter most.",
        "More inventory means buyers take their time — staging makes the difference.",
    ],

    # buyer archetype intros
    "buyer_gen_z": [
        "Gen Z buyers are entering the market here in force.",
        "First-time buyers under 25 are the most active demographic in this zip.",
    ],
    "buyer_younger_millennial": [
        "Younger Millennials — career-focused, often first-time buyers — dominate this market.",
        "The typical buyer here is a Younger Millennial: value-driven, move-in ready preferred.",
    ],
    "buyer_older_millennial": [
        "Older Millennials trading up or planting roots are the primary buyers in this area.",
        "The market here is led by Older Millennials — dual-income households ready to commit.",
    ],
    "buyer_gen_x": [
        "Gen X buyers — established, family-focused, detail-oriented — lead activity here.",
        "The dominant buyer is Gen X: experienced, knows what they want, not easily impressed.",
    ],
    "buyer_boomer": [
        "Boomer buyers drive this market — downsizing or right-sizing, they prioritize quality.",
        "Baby Boomers are the most active buyers here: comfort, low maintenance, and light over square footage.",
    ],
    "buyer_silent": [
        "Older buyers seeking stability and ease are most active in this area.",
    ],
    "buyer_mixed": [
        "A mixed buyer pool spans multiple age groups.",
    ],

    # style recommendations
    "style_transitional": [
        "Transitional staging — clean lines, warm neutrals, neither too modern nor too traditional — is the market's top performer.",
        "Transitional sells best here: timeless without being dated, fresh without being cold.",
    ],
    "style_contemporary": [
        "Contemporary staging commands the highest price per sqft in this market.",
        "Clean, uncluttered Contemporary interiors resonate with the buyer pool here.",
    ],
    "style_modern": [
        "Modern staging — minimal, high-contrast, architectural — stands out in this zip.",
        "Buyers here respond to Modern staging: bold, intentional, and visually sharp.",
    ],
    "style_scandinavian": [
        "Scandinavian staging — light woods, white walls, functional simplicity — performs well in this market.",
    ],
    "style_bohemian": [
        "Bohemian styling resonates with the creative, individualistic buyers active here.",
    ],
    "style_industrial": [
        "Industrial elements — exposed materials, raw textures — connect with the buyers in this market.",
    ],
    "style_coastal": [
        "Coastal staging brings warmth and ease that buyers here respond to.",
    ],
    "style_farmhouse": [
        "Farmhouse warmth and livability connects with the family-oriented buyers in this market.",
    ],
    "style_traditional": [
        "Traditional staging signals quality and longevity — a strong match for the established buyers here.",
    ],
    "style_other": [
        "Staging aligned with local buyer preferences will support a strong result.",
    ],

    # income / price tier
    "price_premium": [
        "Buyers in this zip are well-qualified — median loan north of $600k.",
        "High-income buyers here expect premium finishes and immaculate presentation.",
        "The local buyer pool is financially strong; perceived value and quality matter.",
    ],
    "price_mid": [
        "Buyers here are practical and value-conscious — clean, functional staging outperforms fussy décor.",
        "Mid-range buyers respond to spaces that feel livable and move-in ready.",
    ],
    "price_entry": [
        "First-time and budget-conscious buyers are the core audience — keep staging approachable and clutter-free.",
    ],

    # closing lines
    "close_fast_market": [
        "In a market this active, staging is about not giving buyers a reason to hesitate.",
        "Speed to market matters here — a well-staged home listed clean will move.",
        "First impressions close deals in this zip; presentation sets the ceiling.",
    ],
    "close_slow_market": [
        "In a slower market, staging is the edge that separates the listings that sell from the ones that linger.",
        "Thoughtful staging turns browsers into buyers when competition gives them options.",
    ],
    "close_balanced": [
        "The right staging signals that this home is worth what it's listed at.",
        "Staging that matches the buyer creates confidence — and confidence closes.",
    ],
}


def _pick(key: str) -> str:
    pool = _POOL.get(key, [])
    if not pool:
        return ""
    return random.choice(pool)


# ── style key mapper ─────────────────────────────────────────────────────────

_STYLE_KEY_MAP: dict[str, str] = {
    "Transitional": "style_transitional",
    "Contemporary": "style_contemporary",
    "Modern": "style_modern",
    "Modern Minimalist": "style_modern",
    "Scandinavian": "style_scandinavian",
    "Bohemian": "style_bohemian",
    "Industrial": "style_industrial",
    "Coastal": "style_coastal",
    "Coastal Modern": "style_coastal",
    "Farmhouse": "style_farmhouse",
    "Traditional": "style_traditional",
    "Rustic": "style_farmhouse",
}

_ARCHETYPE_KEY_MAP: dict[str, str] = {
    "Gen Z": "buyer_gen_z",
    "Younger Millennial": "buyer_younger_millennial",
    "Older Millennial": "buyer_older_millennial",
    "Gen X": "buyer_gen_x",
    "Boomer": "buyer_boomer",
    "Silent Generation": "buyer_silent",
    "Mixed": "buyer_mixed",
}


# ── main builder ─────────────────────────────────────────────────────────────

def build_prescription(analysis: dict[str, Any]) -> dict[str, Any]:
    """
    Return a structured listing prescription from pre-computed analysis.

    Output:
      {
        "recommended_style": str,
        "headline": str,
        "body": str,           # 3-4 sentence staging narrative
        "bullet_points": [...], # 3-5 concise staging directives
        "buyer_profile_note": str,
        "signals_used": {...},
      }
    """
    # ── unpack signals ───────────────────────────────────────────────────────
    walk_data = analysis.get("walk_score_data") or {}
    walk = walk_data.get("walk_score") or 0
    transit = walk_data.get("transit_score") or 0

    school = analysis.get("school_profile") or {}
    school_score = school.get("quality_score") or 0
    has_elementary = school.get("has_elementary", False)

    redfin = analysis.get("redfin_market") or {}
    dom = redfin.get("median_dom")

    hmda = analysis.get("hmda_buyer_data") or {}
    archetype = hmda.get("buyer_archetype", "Mixed")
    median_loan_k = hmda.get("median_loan_k") or 0
    median_income_k = hmda.get("median_income_k") or 0

    styles = analysis.get("recommended_styles") or []
    top_style = styles[0]["style"] if styles else None

    zipcode = analysis.get("zipcode", "")
    objective = analysis.get("objective", "balanced")

    # ── walk key ─────────────────────────────────────────────────────────────
    if walk >= 90:
        walk_key = "walk_paradise"
    elif walk >= 70:
        walk_key = "walk_very"
    elif walk >= 50:
        walk_key = "walk_somewhat"
    else:
        walk_key = "walk_car"

    # ── market speed key ─────────────────────────────────────────────────────
    if dom is None:
        market_key = "market_moderate"
        close_key = "close_balanced"
    elif dom <= 20:
        market_key = "market_fast"
        close_key = "close_fast_market"
    elif dom <= 45:
        market_key = "market_moderate"
        close_key = "close_balanced"
    else:
        market_key = "market_slow"
        close_key = "close_slow_market"

    # ── price tier key ───────────────────────────────────────────────────────
    if median_loan_k >= 600 or median_income_k >= 180:
        price_key = "price_premium"
    elif median_loan_k >= 350 or median_income_k >= 100:
        price_key = "price_mid"
    else:
        price_key = "price_entry"

    # ── style key ────────────────────────────────────────────────────────────
    style_key = _STYLE_KEY_MAP.get(top_style, "style_other") if top_style else "style_other"
    archetype_key = _ARCHETYPE_KEY_MAP.get(archetype, "buyer_mixed")

    # ── headline ─────────────────────────────────────────────────────────────
    headline = _build_headline(top_style, archetype, zipcode)

    # ── body (3-4 sentences) ─────────────────────────────────────────────────
    sentences = []

    # 1. Buyer context
    buyer_sent = _pick(archetype_key)
    if buyer_sent:
        sentences.append(buyer_sent)

    # 2. Walkability / neighborhood character
    walk_sent = _pick(walk_key)
    if walk_sent:
        if transit >= 50 and walk < 90:
            walk_sent += " " + _pick("transit_good")
        sentences.append(walk_sent)

    # 3. Market tempo
    sentences.append(_pick(market_key))

    # 4. Style recommendation
    style_sent = _pick(style_key)
    if style_sent:
        sentences.append(style_sent)

    # 5. Closing
    sentences.append(_pick(close_key))

    body = " ".join(s for s in sentences if s)

    # ── bullet points ─────────────────────────────────────────────────────────
    bullets = _build_bullets(
        top_style=top_style,
        walk=walk,
        school_score=school_score,
        has_elementary=has_elementary,
        archetype=archetype,
        price_key=price_key,
        dom=dom,
        median_loan_k=median_loan_k,
    )

    # ── buyer profile note ───────────────────────────────────────────────────
    buyer_note = _build_buyer_note(hmda, archetype)

    return {
        "recommended_style": top_style or "Neutral / Transitional",
        "headline": headline,
        "body": body,
        "bullet_points": bullets,
        "buyer_profile_note": buyer_note,
        "signals_used": {
            "walk_score": walk,
            "transit_score": transit,
            "median_dom": dom,
            "median_loan_k": median_loan_k,
            "median_income_k": median_income_k,
            "buyer_archetype": archetype,
            "top_style": top_style,
            "school_quality_score": school_score,
        },
    }


def _build_headline(style: Optional[str], archetype: str, zipcode: str) -> str:
    style_label = style or "Neutral"
    archetype_short = {
        "Gen Z": "first-time buyers",
        "Younger Millennial": "millennial buyers",
        "Older Millennial": "move-up buyers",
        "Gen X": "established buyers",
        "Boomer": "downsizing buyers",
        "Silent Generation": "mature buyers",
        "Mixed": "a diverse buyer pool",
    }.get(archetype, "local buyers")
    return f"{style_label} staging for {archetype_short} in {zipcode}"


def _build_bullets(
    top_style: Optional[str],
    walk: int,
    school_score: float,
    has_elementary: bool,
    archetype: str,
    price_key: str,
    dom: Optional[float],
    median_loan_k: float,
) -> list[str]:
    bullets: list[str] = []

    # Style direction
    style_directions = {
        "Transitional": "Warm neutrals, mixed materials — avoid stark white or all-grey palettes",
        "Contemporary": "Clean lines, minimal accessories, high-contrast accents",
        "Modern": "Architectural simplicity — let form and light do the work",
        "Modern Minimalist": "Less is more: remove, don't add",
        "Scandinavian": "Light wood tones, white walls, functional pieces only",
        "Bohemian": "Layered textures, earthy tones, curated personal touches",
        "Industrial": "Expose raw materials, keep palette dark and grounded",
        "Coastal": "Blues, whites, natural textures — light and airy throughout",
        "Coastal Modern": "Breezy palette with clean modern lines",
        "Farmhouse": "Warm whites, natural wood, simple vintage accents",
        "Traditional": "Rich woods, classic patterns, symmetry in furniture layout",
        "Rustic": "Natural materials, warm tones, hand-crafted accents",
    }
    if top_style and top_style in style_directions:
        bullets.append(style_directions[top_style])

    # Finish level based on price tier
    if price_key == "price_premium":
        bullets.append("Premium finishes expected — upgrade hardware, art, and soft goods")
    elif price_key == "price_mid":
        bullets.append("Mid-grade finishes: consistent quality throughout, no mismatched updates")
    else:
        bullets.append("Spotless and decluttered beats expensive — focus on clean and bright")

    # School / family signal
    if has_elementary and archetype in ("Gen X", "Older Millennial", "Boomer"):
        bullets.append("Highlight a dedicated kids' room or office — family buyers are active here")
    elif has_elementary:
        bullets.append("Family-functional spaces will resonate — show flexible room use")

    # Walkability highlight
    if walk >= 70:
        bullets.append("Leverage the walk score in the listing — mention nearby shops, restaurants, or transit by name")

    # Market pace
    if dom is not None and dom <= 20:
        bullets.append("Fast market: list clean, price sharp, skip drawn-out staged shoots")
    elif dom is not None and dom > 45:
        bullets.append("Slower pace means buyers will scrutinize — invest in professional photography and thorough staging")

    # Buyer-specific angle
    if archetype in ("Gen Z", "Younger Millennial"):
        bullets.append("Home office nook or flexible workspace is a plus — this buyer works remote or hybrid")
    elif archetype == "Boomer":
        bullets.append("Emphasize single-level living or easy-care features where available")

    return bullets[:6]  # cap at 6 bullets


def _build_buyer_note(hmda: dict, archetype: str) -> str:
    income = hmda.get("median_income_k")
    loan = hmda.get("median_loan_k")
    age_group = hmda.get("dominant_age_group", "")
    pct_u45 = hmda.get("pct_age_under_45")

    parts = []
    if archetype and archetype != "Mixed":
        parts.append(f"Dominant buyer: {archetype} ({age_group})")
    if income:
        parts.append(f"Median household income ${income:.0f}k")
    if loan:
        parts.append(f"median loan ${loan:.0f}k")
    if pct_u45 is not None:
        parts.append(f"{pct_u45:.0f}% of buyers under 45")

    return " · ".join(parts) if parts else "Buyer profile data unavailable."
