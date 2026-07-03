from __future__ import annotations

"""
Neighborhood Data — buyer-facing "what's it like to live here" signals for a
property address: nearby amenities, walkability, and a grounded narrative.

Mirrors the "Neighborhood Overview / Amenities & Lifestyle / Getting Around"
sections of the target Listing Review (demo_report.pdf).

Data sources (NO paid Google dependency — the project's GOOGLE_MAPS_API_KEY is
currently invalid, and key-free OSM also fits the "self-hosted, not paid-API-
dependent" direction):
  - Geocode:   OpenStreetMap Nominatim (address -> lat/lon), pgeocode fallback.
  - Amenities: OpenStreetMap Overpass (named POIs near the point), key-free.
  - Walkability: Walk Score API (reuses walkscore_data, WALKSCORE_API_KEY).

Everything degrades gracefully: a failed source is omitted, never fatal. Results
are cached under data/neighborhood_{key}.json (30-day TTL — amenities are stable).

The LLM narrative (generate_narrative_openai) is a single text-LLM call in the
same spirit as gpt_explainer: it may only restate the provided data, never invent
places, distances, or facts.
"""

import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx
import pgeocode

from app.services.walkscore_data import get_walk_scores

logger = logging.getLogger(__name__)

BI_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BI_ROOT / "data"
CACHE_MAX_AGE_DAYS = 30
_USER_AGENT = "edensign-bi/1.0 (neighborhood analysis; welcome@edensign.io)"

_nomi = pgeocode.Nominatim("us")
_CACHE: dict[str, dict] = {}

# Overpass amenity buckets: (category, [(osm_key, osm_value), ...]).
# Order = display priority in the report's Amenities & Lifestyle section.
_POI_CATEGORIES: list[tuple[str, list[tuple[str, str]]]] = [
    ("dining",     [("amenity", "restaurant"), ("amenity", "cafe"), ("amenity", "fast_food")]),
    ("recreation", [("leisure", "park"), ("leisure", "nature_reserve"),
                    ("leisure", "sports_centre"), ("leisure", "golf_course")]),
    ("grocery",    [("shop", "supermarket"), ("shop", "grocery"), ("shop", "convenience")]),
    ("fitness",    [("leisure", "fitness_centre")]),
    ("schools",    [("amenity", "school")]),
    ("shopping",   [("shop", "mall"), ("shop", "department_store")]),
    ("transit",    [("public_transport", "station"), ("railway", "station"),
                    ("amenity", "bus_station")]),
]


def _haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.7613  # earth radius in miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def geocode_address(address: Optional[str], zipcode: Optional[str]) -> Optional[dict]:
    """Resolve a precise lat/lon + place label. Nominatim first, ZIP centroid fallback."""
    if address:
        try:
            with httpx.Client(timeout=15.0, headers={"User-Agent": _USER_AGENT}) as client:
                r = client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"format": "json", "q": address, "limit": 1, "addressdetails": 1},
                )
                if r.status_code == 200 and r.json():
                    top = r.json()[0]
                    addr = top.get("address", {})
                    return {
                        "lat": float(top["lat"]),
                        "lon": float(top["lon"]),
                        "label": top.get("display_name", address),
                        "city": addr.get("city") or addr.get("town") or addr.get("village"),
                        "state": addr.get("state"),
                        "source": "nominatim",
                    }
        except Exception as exc:
            logger.warning("Neighborhood: Nominatim geocode failed for %r: %s", address, exc)

    if zipcode:
        try:
            rec = _nomi.query_postal_code(str(zipcode)[:5])
            if rec is not None and not math.isnan(float(rec.latitude)):
                return {
                    "lat": float(rec.latitude),
                    "lon": float(rec.longitude),
                    "label": f"{rec.place_name}, {rec.state_code} {zipcode}",
                    "city": rec.place_name,
                    "state": rec.state_code,
                    "source": "pgeocode",
                }
        except Exception as exc:
            logger.warning("Neighborhood: pgeocode fallback failed for %s: %s", zipcode, exc)
    return None


def fetch_pois(lat: float, lon: float, radius_m: int = 4000, per_category: int = 6) -> dict:
    """Query OSM Overpass for named POIs near (lat, lon), bucketed by category.

    Returns {category: [{name, kind, distance_mi}, ...]} sorted by distance,
    plus a flat count. Empty dict on failure (caller degrades gracefully).
    """
    selectors = []
    for _, tags in _POI_CATEGORIES:
        for k, v in tags:
            # nodes and ways (ways via 'out center' to get a coordinate)
            selectors.append(f'node["{k}"="{v}"](around:{radius_m},{lat},{lon});')
            selectors.append(f'way["{k}"="{v}"](around:{radius_m},{lat},{lon});')
    query = f"[out:json][timeout:25];({''.join(selectors)});out center tags 200;"

    try:
        with httpx.Client(timeout=30.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = client.post("https://overpass-api.de/api/interpreter", data={"data": query})
            r.raise_for_status()
            elements = r.json().get("elements", [])
    except Exception as exc:
        logger.warning("Neighborhood: Overpass query failed: %s", exc)
        return {}

    # map (osm_key, osm_value) -> category for bucketing
    tag_to_cat: dict[tuple[str, str], str] = {}
    for cat, tags in _POI_CATEGORIES:
        for kv in tags:
            tag_to_cat[kv] = cat

    buckets: dict[str, list[dict]] = {cat: [] for cat, _ in _POI_CATEGORIES}
    seen: set[tuple[str, str]] = set()
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        # element coordinate (node has lat/lon; way has 'center')
        e_lat = el.get("lat") or el.get("center", {}).get("lat")
        e_lon = el.get("lon") or el.get("center", {}).get("lon")
        if e_lat is None or e_lon is None:
            continue
        cat = None
        kind = None
        for kv, c in tag_to_cat.items():
            if tags.get(kv[0]) == kv[1]:
                cat, kind = c, kv[1]
                break
        if cat is None:
            continue
        dedup = (cat, name.lower())
        if dedup in seen:
            continue
        seen.add(dedup)
        buckets[cat].append({
            "name": name,
            "kind": kind,
            "distance_mi": round(_haversine_mi(lat, lon, float(e_lat), float(e_lon)), 2),
        })

    result = {}
    total = 0
    for cat, items in buckets.items():
        if not items:
            continue
        items.sort(key=lambda x: x["distance_mi"])
        result[cat] = items[:per_category]
        total += len(result[cat])
    result["_total"] = total
    return result


def _cache_key(address: Optional[str], zipcode: Optional[str]) -> str:
    if address:
        import hashlib
        return "addr_" + hashlib.md5(address.lower().strip().encode()).hexdigest()[:10]
    return f"zip_{str(zipcode)[:5]}"


def analyze_neighborhood(
    address: Optional[str] = None,
    zipcode: Optional[str] = None,
    radius_m: int = 4000,
) -> dict:
    """Assemble structured neighborhood data (geocode + amenities + walkability).

    Returns a dict ready for the LLM narrator and the frontend. Cached 30 days.
    Does NOT call the LLM — see generate_narrative_openai for that.
    """
    key = _cache_key(address, zipcode)
    if key in _CACHE and _CACHE[key].get("amenities"):  # ignore poisoned (empty) cache → refetch
        return _CACHE[key]
    cache_file = DATA_DIR / f"neighborhood_{key}.json"
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) <= CACHE_MAX_AGE_DAYS * 86400:
        try:
            data = json.loads(cache_file.read_text())
            if data.get("amenities"):  # skip empties from a past Overpass failure
                _CACHE[key] = data
                return data
        except Exception:
            pass

    loc = geocode_address(address, zipcode)
    out: dict[str, Any] = {
        "address": address,
        "zipcode": zipcode,
        "location": loc,
        "amenities": {},
        "walk_score": None,
    }
    if loc:
        out["amenities"] = fetch_pois(loc["lat"], loc["lon"], radius_m=radius_m)
        try:
            ws = get_walk_scores(
                zipcode or "", lat=loc["lat"], lon=loc["lon"], address_string=address
            )
            if ws:
                out["walk_score"] = {
                    "walk": ws.get("walk_score"),
                    "transit": ws.get("transit_score"),
                    "bike": ws.get("bike_score"),
                    "description": ws.get("description"),
                }
        except Exception as exc:
            logger.warning("Neighborhood: walk score failed: %s", exc)

    # Only cache a successful amenities fetch — don't let a transient Overpass failure
    # (empty amenities) get pinned for 30 days; an empty result will simply retry.
    if out["amenities"]:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            cache_file.write_text(json.dumps(out, separators=(",", ":")))
        except Exception:
            pass
        _CACHE[key] = out
    return out


# ---- LLM narrative (text-only, grounded; same discipline as gpt_explainer) ----

def _narrative_system_prompt() -> str:
    return (
        "You are a real-estate copywriter producing the neighborhood section of a "
        "listing review. Write warm, concrete, buyer-facing prose. CRITICAL: use ONLY "
        "the places, distances, and scores in the provided JSON. Never invent a business "
        "name, landmark, school rating, commute time, or statistic that is not in the "
        "data. If a category is empty, simply omit it. Return valid JSON only."
    )


def _narrative_user_prompt(data: dict, market: Optional[dict]) -> str:
    payload = {
        "neighborhood_data": data,
        "market_context": market or {},
        "required_output_schema": {
            "overview": "2-3 sentences on the area's character, grounded in the city/"
                        "state and walk score. No invented landmarks.",
            "amenities_lifestyle": "2-4 sentences naming actual nearby places from "
                                   "amenities (with their distances in miles) across "
                                   "dining/recreation/grocery/fitness. Only real names.",
            "getting_around": "1-2 sentences from walk_score (walk/transit/bike). If "
                              "transit is low, say it's car-oriented. Empty string if no "
                              "walk_score.",
            "market_note": "1 sentence on market_context (median price / days on market) "
                           "if present, else empty string.",
        },
        "rules": [
            "English only.",
            "Every place name and distance you write MUST appear in neighborhood_data.amenities.",
            "Do not attribute data to sources (no 'according to OpenStreetMap').",
            "If amenities is empty/sparse, keep amenities_lifestyle short and factual; do "
            "not pad with invented places.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


async def generate_narrative_openai(
    data: dict, market: Optional[dict] = None
) -> dict:
    """Single text-LLM call that writes the grounded neighborhood narrative."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is missing in environment.")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _narrative_system_prompt()},
            {"role": "user", "content": _narrative_user_prompt(data, market)},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(base_url + "/chat/completions", headers=headers, json=body)
        resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return {"provider": "openai", "model": model, "narrative": json.loads(content)}
