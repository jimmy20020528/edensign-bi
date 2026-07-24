from __future__ import annotations

"""
Walk Score Data — walkability, transit, and bike scores for any US ZIP.

Data source: Walk Score Professional API (free 5000 calls/day).
  Endpoint: https://api.walkscore.com/score?format=json&...

Scores range 0–100:
  Walk Score:    0=car-dependent, 100=walker's paradise
  Transit Score: 0=minimal, 100=rider's paradise
  Bike Score:    0=bikeable only on trails, 100=biker's paradise

Cache: data/walkscore_{zip}.json, 90-day TTL (amenity density rarely changes)
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx
import pgeocode

from app.services.public_data_proxy import public_data_proxy

logger = logging.getLogger(__name__)

BI_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BI_ROOT / "data"
CACHE_MAX_AGE_DAYS = 90
API_URL = "https://api.walkscore.com/score"

_nomi = pgeocode.Nominatim("us")
_CACHE: dict[str, dict] = {}


def _cache_path(zipcode: str) -> Path:
    return DATA_DIR / f"walkscore_{zipcode}.json"


def _cache_is_stale(zipcode: str) -> bool:
    p = _cache_path(zipcode)
    if not p.exists():
        return True
    return (time.time() - p.stat().st_mtime) > CACHE_MAX_AGE_DAYS * 86400


def _cache_key(zipcode: str, lat: Optional[float], lon: Optional[float]) -> str:
    if lat is not None and lon is not None:
        return f"{zipcode}_{lat:.4f}_{lon:.4f}"
    return zipcode


def _fetch(
    zipcode: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    address_string: Optional[str] = None,
) -> Optional[dict]:
    api_key = os.environ.get("WALKSCORE_API_KEY")
    if not api_key:
        logger.warning("WALKSCORE_API_KEY not set")
        return None

    params: dict = {"format": "json", "transit": 1, "bike": 1, "wsapikey": api_key}

    # Walk Score API always requires lat/lon.
    # When we have a real address string, use it as the address param (better label in response).
    # Always fill lat/lon: prefer geocoded coords, fall back to ZIP centroid via pgeocode.
    if address_string:
        params["address"] = address_string

    if lat is None or lon is None:
        try:
            row = _nomi.query_postal_code(zipcode)
            lat = lat or float(row.get("latitude", 0) or 0)
            lon = lon or float(row.get("longitude", 0) or 0)
            if not address_string:
                city = str(row.get("place_name", "")).strip()
                state = str(row.get("state_code", "")).strip()
                params["address"] = f"{city}, {state} {zipcode}"
        except Exception as exc:
            logger.warning("WalkScore: pgeocode failed for ZIP %s: %s", zipcode, exc)
            return None

    if not lat or not lon:
        logger.warning("WalkScore: no coordinates for ZIP %s", zipcode)
        return None

    params["lat"] = lat
    params["lon"] = lon

    try:
        with httpx.Client(timeout=15.0, proxy=public_data_proxy()) as client:
            resp = client.get(API_URL, params=params)
            resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("WalkScore: API call failed for ZIP %s: %s", zipcode, exc)
        return None

    if data.get("status") not in (1, 2):
        logger.warning("WalkScore: unexpected status %s for ZIP %s", data.get("status"), zipcode)
        return None

    return {
        "walk_score":    data.get("walkscore"),
        "walk_desc":     data.get("description", ""),
        "transit_score": data.get("transit", {}).get("score") if data.get("transit") else None,
        "transit_desc":  data.get("transit", {}).get("description", "") if data.get("transit") else "",
        "bike_score":    data.get("bike", {}).get("score") if data.get("bike") else None,
        "bike_desc":     data.get("bike", {}).get("description", "") if data.get("bike") else "",
    }


def get_walk_scores(
    zipcode: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    address_string: Optional[str] = None,
) -> Optional[dict]:
    """
    Return walk/transit/bike scores.

    When address_string is provided, Walk Score geocodes it internally (matches
    website scores exactly). Otherwise falls back to ZIP centroid via pgeocode.
    """
    zipcode = str(zipcode).strip()[:5]
    # Cache key: address-based queries keyed by address hash, ZIP queries by ZIP
    if address_string:
        import hashlib
        key = f"{zipcode}_addr_{hashlib.md5(address_string.lower().encode()).hexdigest()[:8]}"
    else:
        key = _cache_key(zipcode, lat, lon)

    if key in _CACHE:
        return _CACHE[key]

    p = DATA_DIR / f"walkscore_{key}.json"
    if p.exists() and (time.time() - p.stat().st_mtime) <= CACHE_MAX_AGE_DAYS * 86400:
        try:
            result = json.loads(p.read_text())
            _CACHE[key] = result
            return result
        except Exception:
            pass

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    result = _fetch(zipcode, lat, lon, address_string)
    if result is not None and result.get("walk_score") is not None:
        p.write_text(json.dumps(result))
        _CACHE[key] = result
        return result
    return None
