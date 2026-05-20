from __future__ import annotations

"""
Geocoder — resolve a US address string to {lat, lon, zipcode, city, state}.

Primary:  US Census Bureau Geocoder (free, no key, official TIGER/Line data)
Fallback: Nominatim / OpenStreetMap (free, no key)

Cache: data/geocode_{hash}.json, 90-day TTL
"""

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BI_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BI_ROOT / "data"
CACHE_MAX_AGE_DAYS = 90

CENSUS_URL = (
    "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
    "?address={address}&benchmark=2020&format=json"
)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

_CACHE: dict[str, dict] = {}


def _cache_path(address: str) -> Path:
    key = hashlib.md5(address.lower().strip().encode()).hexdigest()[:12]
    return DATA_DIR / f"geocode_{key}.json"


def _cache_is_stale(address: str) -> bool:
    p = _cache_path(address)
    if not p.exists():
        return True
    return (time.time() - p.stat().st_mtime) > CACHE_MAX_AGE_DAYS * 86400


def _extract_zip_from_components(components: list[dict]) -> Optional[str]:
    for c in components:
        if "Zip" in c.get("componentType", ""):
            return str(c.get("value", "")).strip()[:5]
    return None


def _try_census(address: str) -> Optional[dict]:
    try:
        url = CENSUS_URL.format(address=httpx.URL(address).raw_path if False else address)
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
                params={"address": address, "benchmark": "2020", "format": "json"},
            )
            resp.raise_for_status()
        data = resp.json()
        matches = data.get("result", {}).get("addressMatches", [])
        if not matches:
            return None
        m = matches[0]
        coords = m.get("coordinates", {})
        lat = float(coords.get("y", 0))
        lon = float(coords.get("x", 0))
        if not lat or not lon:
            return None
        # Extract ZIP from address components
        comps = m.get("addressComponents", {})
        zipcode = str(comps.get("zip", "")).strip()[:5]
        city = str(comps.get("city", "")).strip()
        state = str(comps.get("state", "")).strip()
        formatted = m.get("matchedAddress", address)
        if not zipcode:
            return None
        return {
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "zipcode": zipcode,
            "city": city,
            "state": state,
            "formatted_address": formatted,
            "source": "census",
        }
    except Exception as exc:
        logger.warning("Census geocoder failed for '%s': %s", address, exc)
        return None


def _try_nominatim(address: str) -> Optional[dict]:
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                NOMINATIM_URL,
                params={
                    "q": address,
                    "countrycodes": "us",
                    "format": "json",
                    "addressdetails": 1,
                    "limit": 1,
                },
                headers={"User-Agent": "Edensign-BI/1.0"},
            )
            resp.raise_for_status()
        results = resp.json()
        if not results:
            return None
        r = results[0]
        lat = float(r.get("lat", 0))
        lon = float(r.get("lon", 0))
        addr = r.get("address", {})
        zipcode = str(addr.get("postcode", "")).strip()[:5]
        city = addr.get("city") or addr.get("town") or addr.get("village") or ""
        state = addr.get("state", "")
        if not zipcode or not lat or not lon:
            return None
        return {
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "zipcode": zipcode,
            "city": str(city),
            "state": str(state),
            "formatted_address": r.get("display_name", address),
            "source": "nominatim",
        }
    except Exception as exc:
        logger.warning("Nominatim geocoder failed for '%s': %s", address, exc)
        return None


def geocode_address(address: str) -> Optional[dict]:
    """
    Resolve a US address string to location data.

    Returns:
      {
        "lat": 42.3534,
        "lon": -71.1305,
        "zipcode": "02134",
        "city": "Boston",
        "state": "MA",
        "formatted_address": "123 Main St, Boston, MA 02134",
        "source": "census" | "nominatim",
      }
    or None if geocoding fails.
    """
    address = address.strip()
    if not address:
        return None

    cache_key = address.lower()
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    p = _cache_path(address)
    if not _cache_is_stale(address):
        try:
            result = json.loads(p.read_text())
            _CACHE[cache_key] = result
            return result
        except Exception:
            pass

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    result = _try_census(address) or _try_nominatim(address)

    if result is not None:
        p.write_text(json.dumps(result))
        _CACHE[cache_key] = result
        logger.info("Geocoded '%s' → %s %s via %s", address, result["zipcode"], result["city"], result["source"])
    else:
        logger.warning("Geocoding failed for '%s'", address)

    return result
