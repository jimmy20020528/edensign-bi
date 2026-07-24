from __future__ import annotations

"""
Redfin Comps — comparable-sales (CMA) data for a subject property.

Powers the "Comparable Properties Analysis" section of the target Listing Review
(demo_report.pdf): a comp set, price range, avg $/SF, a suggested list range +
anchor, and (optionally) a grounded narrative.

Data source: Redfin's `stingray/api/gis-csv` download (the same CSV behind the
"Download All" button on a Redfin search). It returns real sold listings with
address / price / beds / baths / sqft / $sf / sold date.

Two-step access (the obvious autocomplete resolver is CloudFront-403'd from
datacenter IPs, so we resolve the region id a different way):
  1. ZIP -> Redfin region_id: scrape it from https://www.redfin.com/zipcode/{zip}
     (that page returns 200 and embeds `region_id=NNN`).
  2. region_id -> comps: gis-csv with region_type=2 (ZIP), status=9 (sold).

This is an UNOFFICIAL endpoint: it can rate-limit or block. Every call degrades
gracefully (returns None / empty), never fatal. Results cached under data/.

NOTE: requires a browser-like User-Agent; without it Redfin 403s.
"""

import csv
import io
import json
import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from app.services.public_data_proxy import public_data_proxy

from app.services.neighborhood_data import geocode_address


def _haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _norm_type(property_type: Optional[str]) -> str:
    """Map a subject property-type hint to a Redfin PROPERTY TYPE substring."""
    p = (property_type or "").lower()
    if "condo" in p or "co-op" in p or "coop" in p:
        return "Condo"
    if "town" in p:
        return "Townhouse"
    if "multi" in p:
        return "Multi-Family"
    if "land" in p or "lot" in p:
        return "Land"
    if "mobile" in p or "manufactured" in p:
        return "Mobile"
    return "Single Family"  # residential / single-family / house / default

logger = logging.getLogger(__name__)

BI_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BI_ROOT / "data"
REGION_TTL_DAYS = 90      # ZIP->region_id mapping never really changes
COMPS_TTL_DAYS = 7        # sold comps refresh weekly

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.redfin.com/",
}

_GIS_CSV = "https://www.redfin.com/stingray/api/gis-csv"
_region_cache: dict[str, Optional[int]] = {}


def resolve_region_id(zipcode: str) -> Optional[int]:
    """ZIP -> Redfin internal region_id (scraped from the zipcode page). Cached."""
    zipcode = str(zipcode).strip()[:5]
    if zipcode in _region_cache:
        return _region_cache[zipcode]

    cache_file = DATA_DIR / f"redfin_region_{zipcode}.json"
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) <= REGION_TTL_DAYS * 86400:
        try:
            rid = json.loads(cache_file.read_text()).get("region_id")
            _region_cache[zipcode] = rid
            return rid
        except Exception:
            pass

    region_id: Optional[int] = None
    try:
        with httpx.Client(timeout=20.0, headers=_HEADERS, follow_redirects=True, proxy=public_data_proxy()) as client:
            r = client.get(f"https://www.redfin.com/zipcode/{zipcode}")
            if r.status_code == 200:
                # the page embeds the region id in several `region_id=NNN` query strings
                hits = re.findall(r"region_id=(\d+)", r.text)
                # the page also contains the literal ZIP as a number; pick the most
                # common non-ZIP id (the real region id repeats across map/url links)
                from collections import Counter
                counts = Counter(h for h in hits if h != zipcode)
                if counts:
                    region_id = int(counts.most_common(1)[0][0])
            else:
                logger.warning("Redfin zipcode page %s returned HTTP %s", zipcode, r.status_code)
    except Exception as exc:
        logger.warning("Redfin region resolve failed for %s: %s", zipcode, exc)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        cache_file.write_text(json.dumps({"region_id": region_id}))
    except Exception:
        pass
    _region_cache[zipcode] = region_id
    return region_id


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None or str(v).strip() == "":
            return None
        return float(str(v).replace(",", "").replace("$", "").strip())
    except Exception:
        return None


def _gis_fetch(region_id: int, status: int, extra: Optional[dict] = None) -> str:
    """Raw gis-csv text for one status. Empty string on failure."""
    params = {
        "al": 1, "num_homes": 350, "region_id": region_id, "region_type": 2,
        "status": status, "uipt": "1,2,3,4,5,6", "v": 8,
    }
    if extra:
        params.update(extra)
    try:
        with httpx.Client(timeout=30.0, headers=_HEADERS, follow_redirects=True, proxy=public_data_proxy()) as client:
            r = client.get(_GIS_CSV, params=params)
            if r.status_code != 200 or not r.text.strip():
                logger.warning("Redfin gis-csv HTTP %s region %s status %s",
                               r.status_code, region_id, status)
                return ""
            return r.text
    except Exception as exc:
        logger.warning("Redfin gis-csv failed region %s status %s: %s", region_id, status, exc)
        return ""


def _parse_comps(text: str, listing_status: str) -> list[dict]:
    comps: list[dict] = []
    for row in csv.DictReader(io.StringIO(text)):
        vals = list(row.values())
        if len(vals) < 17 or not vals[3]:   # vals[3] = ADDRESS; skip disclaimer/blank
            continue
        comps.append({
            "sold_date": vals[1] or None,
            "property_type": vals[2] or None,
            "address": vals[3],
            "city": vals[4],
            "state": vals[5],
            "zip": vals[6],
            "price": _to_float(vals[7]),
            "beds": _to_float(vals[8]),
            "baths": _to_float(vals[9]),
            "sqft": _to_float(vals[11]),
            "lot_size": _to_float(vals[12]),
            "year_built": _to_float(vals[13]),
            "dom": _to_float(vals[14]),
            "ppsf": _to_float(vals[15]),
            "url": vals[20] if len(vals) > 20 else None,
            "latitude": _to_float(vals[25]) if len(vals) > 25 else None,
            "longitude": _to_float(vals[26]) if len(vals) > 26 else None,
            "listing_status": listing_status,
        })
    return comps


def fetch_comps(region_id: int, sold_within_days: int = 365) -> list[dict]:
    """Recently-sold (last year) + currently-active listings, deduped by address.

    The PDF CMA uses "recently sold AND active listings near the subject", so we
    pull both: status=9 (sold, time-bounded) and status=1 (active).
    """
    sold = _parse_comps(_gis_fetch(region_id, 9, {"sold_within_days": sold_within_days}), "sold")
    active = _parse_comps(_gis_fetch(region_id, 1), "active")
    seen: set[str] = set()
    out: list[dict] = []
    for c in sold + active:
        key = (c.get("address") or "").lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _similarity(comp: dict, beds, baths, sqft, year_built=None) -> float:
    """Lower is more similar. Distance in (sqft, beds, baths, age) space, sqft-dominant."""
    score = 0.0
    if sqft and comp.get("sqft"):
        score += abs(comp["sqft"] - sqft) / max(sqft, 1) * 3.0
    if beds is not None and comp.get("beds") is not None:
        score += abs(comp["beds"] - beds) * 0.5
    if baths is not None and comp.get("baths") is not None:
        score += abs(comp["baths"] - baths) * 0.3
    if year_built and comp.get("year_built"):
        score += abs(comp["year_built"] - year_built) / 30.0  # ~30yr span ≈ one size-unit
    return score


def analyze_comps(
    zipcode: str,
    address: Optional[str] = None,
    beds: Optional[float] = None,
    baths: Optional[float] = None,
    sqft: Optional[float] = None,
    listing_price: Optional[float] = None,
    property_type: Optional[str] = None,
    year_built: Optional[float] = None,
    top_n: int = 10,
    min_comps: int = 6,
) -> dict:
    """Assemble a CMA: PDF-style comp selection + $/SF stats + suggested range.

    Selection (the part that matters): same property type, similar size (±band)
    and beds, near the subject (sorted by distance), recent sold + active. Loosens
    via a relaxation ladder until at least `min_comps` qualify. Cached 7d.

    Does NOT call the LLM — see generate_comps_narrative_openai.
    """
    zipcode = str(zipcode).strip()[:5]
    cache_file = DATA_DIR / f"redfin_comps_v2_{zipcode}.json"
    raw_comps: Optional[list[dict]] = None
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) <= COMPS_TTL_DAYS * 86400:
        try:
            raw_comps = json.loads(cache_file.read_text())
        except Exception:
            raw_comps = None

    region_id = resolve_region_id(zipcode)
    if raw_comps is None:
        raw_comps = fetch_comps(region_id) if region_id else []
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            cache_file.write_text(json.dumps(raw_comps, separators=(",", ":")))
        except Exception:
            pass

    # Subject location → per-comp distance (proximity is the PDF's first lever).
    subj = geocode_address(address, zipcode)
    slat, slon = (subj["lat"], subj["lon"]) if subj else (None, None)
    for c in raw_comps:
        if slat is not None and c.get("latitude") is not None and c.get("longitude") is not None:
            c["distance_mi"] = round(_haversine_mi(slat, slon, c["latitude"], c["longitude"]), 2)
        else:
            c["distance_mi"] = None

    usable = [c for c in raw_comps if c.get("price") and c.get("sqft")]
    want_type = _norm_type(property_type)

    # Relaxation ladder: tighten first (same type, ±25% sqft, ±1 bed), loosen only
    # if we can't reach min_comps — so thin/rural markets still return something.
    tiers = [
        {"sqft_pct": 0.25, "beds_tol": 1, "year_tol": 15, "type": True,  "label": "similar size/age · same type"},
        {"sqft_pct": 0.40, "beds_tol": 2, "year_tol": 30, "type": True,  "label": "wider size/age · same type"},
        {"sqft_pct": 0.40, "beds_tol": 2, "year_tol": None, "type": False, "label": "wider size · any type"},
        {"sqft_pct": None, "beds_tol": None, "year_tol": None, "type": False, "label": "all recent listings nearby"},
    ]
    selected: list[dict] = []
    tier_label = tiers[-1]["label"]
    for t in tiers:
        cand = usable
        if t["type"]:
            cand = [c for c in cand if want_type in (c.get("property_type") or "")]
        if t["sqft_pct"] and sqft:
            cand = [c for c in cand if c.get("sqft") and abs(c["sqft"] - sqft) / sqft <= t["sqft_pct"]]
        if t["beds_tol"] is not None and beds is not None:
            cand = [c for c in cand if c.get("beds") is not None and abs(c["beds"] - beds) <= t["beds_tol"]]
        if t["year_tol"] is not None and year_built:
            cand = [c for c in cand if c.get("year_built") and abs(c["year_built"] - year_built) <= t["year_tol"]]
        if len(cand) >= min_comps or t is tiers[-1]:
            selected, tier_label = cand, t["label"]
            break

    # Order: nearest first when we have coordinates, else by size/bed/age similarity.
    if any(c.get("distance_mi") is not None for c in selected):
        selected = sorted(selected, key=lambda c: (c.get("distance_mi") is None,
                                                   c.get("distance_mi") if c.get("distance_mi") is not None else 1e9))
    elif sqft or beds is not None or year_built:
        selected = sorted(selected, key=lambda c: _similarity(c, beds, baths, sqft, year_built))
    top = selected[:top_n]

    # ── Highlights: tag the comp closest to the subject on each dimension, and
    # pull the single best overall match to the front. Only dimensions whose
    # subject spec is known get a tag (so we never claim a closeness we can't compute).
    for c in top:
        c["badges"] = []
    if sqft:
        cand = [c for c in top if c.get("sqft")]
        if cand:
            b = min(cand, key=lambda c: abs(c["sqft"] - sqft))
            d = round(b["sqft"] - sqft)
            b["badges"].append({"key": "size", "label": f"Closest size · {'+' if d >= 0 else '−'}{abs(d)} sqft"})
    if year_built:
        cand = [c for c in top if c.get("year_built")]
        if cand:
            b = min(cand, key=lambda c: abs(c["year_built"] - year_built))
            d = int(abs(b["year_built"] - year_built))
            b["badges"].append({"key": "year", "label": f"Closest age · {d} yr{'s' if d != 1 else ''}"})
    if len({(c.get("property_type") or "") for c in top}) > 1:  # only meaningful in mixed-type sets
        for c in top:
            if want_type in (c.get("property_type") or ""):
                c["badges"].append({"key": "type", "label": "Same type"})
    best_overall = None
    if top and (sqft or beds is not None or year_built):
        best_overall = min(top, key=lambda c: _similarity(c, beds, baths, sqft, year_built))
        best_overall["badges"].insert(0, {"key": "best", "label": "Best match"})
        top = [best_overall] + [c for c in top if c is not best_overall]  # pull to front

    ppsfs = sorted(c["ppsf"] for c in top if c.get("ppsf"))
    prices = sorted(c["price"] for c in top if c.get("price"))
    doms = [c["dom"] for c in top if c.get("dom") is not None]

    def _median(xs):
        if not xs:
            return None
        n = len(xs)
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2

    median_ppsf = _median(ppsfs)
    suggested = None
    if median_ppsf and sqft:
        anchor = round(median_ppsf * sqft, -3)        # round to nearest $1k
        suggested = {
            "anchor": anchor,
            "low": round(anchor * 0.97, -3),
            "high": round(anchor * 1.04, -3),
            "basis_ppsf": round(median_ppsf, 2),
        }

    subject_ppsf = round(listing_price / sqft, 2) if (listing_price and sqft) else None
    position = None
    if subject_ppsf and median_ppsf:
        delta = (subject_ppsf - median_ppsf) / median_ppsf
        position = {
            "subject_ppsf": subject_ppsf,
            "market_ppsf": round(median_ppsf, 2),
            "delta_pct": round(delta * 100, 1),
            "label": "above market" if delta > 0.03 else ("below market" if delta < -0.03 else "at market"),
        }

    n_active = sum(1 for c in top if c.get("listing_status") == "active")
    return {
        "zipcode": zipcode,
        "region_id": region_id,
        "subject": {"beds": beds, "baths": baths, "sqft": sqft, "listing_price": listing_price,
                    "ppsf": subject_ppsf, "property_type": want_type, "year_built": year_built},
        "comp_count": len(top),
        "total_found": len(usable),
        "selection": {
            "tier": tier_label,            # which relaxation tier was used
            "pool": len(usable),           # total recent sold+active in the ZIP
            "matched": len(selected),      # passed the (final) tier's filters
            "active": n_active,            # of the shown comps, how many are active
            "located": subj is not None,   # did we geocode the subject (proximity)?
        },
        "price_range": {"low": prices[0], "high": prices[-1]} if prices else None,
        "avg_ppsf": round(sum(ppsfs) / len(ppsfs), 2) if ppsfs else None,
        "median_ppsf": round(median_ppsf, 2) if median_ppsf else None,
        "avg_dom": round(sum(doms) / len(doms)) if doms else None,
        "suggested_range": suggested,
        "price_position": position,
        "highlights": {
            "best_overall": best_overall["address"] if best_overall else None,
            "dimensions": [b["label"] for c in top for b in c.get("badges", []) if b["key"] != "best"],
        },
        "comps": top,
    }


# ---- LLM narrative (grounded; same discipline as gpt_explainer / neighborhood) ----

def _narr_system() -> str:
    return (
        "You are a real-estate pricing analyst writing the Comparable Properties section "
        "of a listing review. Use ONLY the comps, prices, $/SF, and ranges in the provided "
        "JSON. Never invent an address, price, or statistic. Be concrete and concise. "
        "Return valid JSON only."
    )


def _narr_user(cma: dict) -> str:
    payload = {
        "cma": cma,
        "required_output_schema": {
            "market_positioning": "2-3 sentences on where the subject sits vs the comp set "
                                  "(use avg/median $/SF, price_range, price_position). No invented numbers.",
            "pricing_strategy": "2-3 sentences recommending a list range/anchor from "
                               "suggested_range, and what would justify the high end.",
            "comp_notes": "array of up to 4 short strings, each comparing ONE real comp "
                         "(by its address) to the subject on size/price/$ per SF.",
        },
        "rules": [
            "English only.",
            "Every address, price, and $/SF you cite MUST be present in cma.comps or cma stats.",
            "If suggested_range or price_position is null, omit pricing claims that need them.",
            "No data-source attribution.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


async def generate_comps_narrative_openai(cma: dict) -> dict:
    """Single text-LLM call writing the grounded CMA narrative."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is missing in environment.")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _narr_system()},
            {"role": "user", "content": _narr_user(cma)},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(base_url + "/chat/completions", headers=headers, json=body)
        resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return {"provider": "openai", "model": model, "narrative": json.loads(content)}
