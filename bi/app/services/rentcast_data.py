from __future__ import annotations

"""
RentCast Data — rental market estimates for any US ZIP.

Data source: RentCast API (free 50 calls/month, paid $29+/mo).
  Endpoint: https://api.rentcast.io/v1/markets?zipCode={zip}

Returns median rent, vacancy rate, price-to-rent ratio, and listing counts.
Used as a display-only signal (not in regression model) to help agents understand
whether this is a buy vs. rent market and gauge investor vs. owner-occupant demand.

Cache: data/rentcast_{zip}.json, 30-day TTL
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BI_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BI_ROOT / "data"
CACHE_MAX_AGE_DAYS = 30
API_URL = "https://api.rentcast.io/v1/markets"

_CACHE: dict[str, dict] = {}


def _cache_path(zipcode: str) -> Path:
    return DATA_DIR / f"rentcast_{zipcode}.json"


def _cache_is_stale(zipcode: str) -> bool:
    p = _cache_path(zipcode)
    if not p.exists():
        return True
    return (time.time() - p.stat().st_mtime) > CACHE_MAX_AGE_DAYS * 86400


def _fetch(zipcode: str) -> Optional[dict]:
    api_key = os.environ.get("RENTCAST_API_KEY")
    if not api_key:
        logger.warning("RENTCAST_API_KEY not set")
        return None

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                API_URL,
                params={"zipCode": zipcode},
                headers={"X-Api-Key": api_key, "Accept": "application/json"},
            )
            resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("RentCast: API call failed for ZIP %s: %s", zipcode, exc)
        return None

    # RentCast /markets returns a single object or an error
    if not isinstance(data, dict) or "averageRent" not in data and "medianRent" not in data:
        logger.warning("RentCast: unexpected response shape for ZIP %s: %s", zipcode, str(data)[:120])
        return None

    median_rent = data.get("medianRent") or data.get("averageRent")
    avg_price = data.get("averageHomeValue")

    price_to_rent = None
    if avg_price and median_rent and median_rent > 0:
        price_to_rent = round(avg_price / (median_rent * 12), 1)

    return {
        "median_rent":      round(float(median_rent), 0) if median_rent else None,
        "avg_home_value":   round(float(avg_price), 0) if avg_price else None,
        "price_to_rent":    price_to_rent,
        "vacancy_rate":     data.get("vacancyRate"),
        "for_sale_count":   data.get("activeSaleListingsCount"),
        "for_rent_count":   data.get("activeRentalListingsCount"),
    }


def get_rent_data(zipcode: str) -> Optional[dict]:
    """
    Return rental market data for the ZIP, or None if unavailable.

    Result dict shape:
      {
        "median_rent":    2800,
        "avg_home_value": 650000,
        "price_to_rent":  19.3,        # < 15 = buy-favored, > 20 = rent-favored
        "vacancy_rate":   0.045,       # 4.5%
        "for_sale_count": 34,
        "for_rent_count": 12,
      }
    """
    zipcode = str(zipcode).strip()[:5]

    if zipcode in _CACHE:
        return _CACHE[zipcode]

    p = _cache_path(zipcode)
    if not _cache_is_stale(zipcode):
        try:
            result = json.loads(p.read_text())
            _CACHE[zipcode] = result
            return result
        except Exception:
            pass

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    result = _fetch(zipcode)
    if result is not None:
        p.write_text(json.dumps(result))
        _CACHE[zipcode] = result
    return result
