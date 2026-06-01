from __future__ import annotations

"""
FRED Macro Data — national and state-level economic indicators for any US ZIP.

Data source: Federal Reserve Economic Data (FRED) API (free, requires key).
  Endpoint: https://api.stlouisfed.org/fred/series/observations

Series fetched:
  - MORTGAGE30US: 30-year fixed mortgage rate (national, weekly)
  - {STATE}UR:    State unemployment rate (monthly), e.g. MAUR for Massachusetts
  - CSUSHPISA:    Case-Shiller National Home Price Index (monthly, seasonally adjusted)
  - HOUST:        Total housing starts (national, monthly)

These are macro context signals. They don't influence staging style rankings
but help agents understand the broader market environment (rate environment,
housing supply, regional employment health).

Cache: data/fred_{series_id}.json per series, 7-day TTL
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx
import pgeocode

logger = logging.getLogger(__name__)

BI_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BI_ROOT / "data"
CACHE_MAX_AGE_DAYS = 7
FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# USPS state abbreviation → FRED unemployment series ID
_STATE_UR_SERIES: dict[str, str] = {
    "AL": "ALUR", "AK": "AKUR", "AZ": "AZUR", "AR": "ARUR", "CA": "CAUR",
    "CO": "COUR", "CT": "CTUR", "DE": "DEUR", "DC": "DCUR", "FL": "FLUR",
    "GA": "GAUR", "HI": "HIUR", "ID": "IDUR", "IL": "ILUR", "IN": "INUR",
    "IA": "IAUR", "KS": "KSUR", "KY": "KYUR", "LA": "LAUR", "ME": "MEUR",
    "MD": "MDUR", "MA": "MAUR", "MI": "MIUR", "MN": "MNUR", "MS": "MSUR",
    "MO": "MOUR", "MT": "MTUR", "NE": "NEUR", "NV": "NVUR", "NH": "NHUR",
    "NJ": "NJUR", "NM": "NMUR", "NY": "NYUR", "NC": "NCUR", "ND": "NDUR",
    "OH": "OHUR", "OK": "OKUR", "OR": "ORUR", "PA": "PAUR", "RI": "RIUR",
    "SC": "SCUR", "SD": "SDUR", "TN": "TNUR", "TX": "TXUR", "UT": "UTUR",
    "VT": "VTUR", "VA": "VAUR", "WA": "WAUR", "WV": "WVUR", "WI": "WIUR",
    "WY": "WYUR",
}

_nomi = pgeocode.Nominatim("us")
_CACHE: dict[str, dict] = {}


def _cache_path(series_id: str) -> Path:
    return DATA_DIR / f"fred_{series_id}.json"


def _cache_is_stale(series_id: str) -> bool:
    p = _cache_path(series_id)
    if not p.exists():
        return True
    return (time.time() - p.stat().st_mtime) > CACHE_MAX_AGE_DAYS * 86400


def _fetch_series_latest(series_id: str) -> Optional[float]:
    """Fetch the most recent non-null observation for a FRED series."""
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        return None

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(FRED_BASE_URL, params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 5,
            })
            resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("FRED: fetch failed for series %s: %s", series_id, exc)
        return None

    for obs in data.get("observations", []):
        val = obs.get("value", ".")
        if val != ".":
            try:
                return float(val)
            except (ValueError, TypeError):
                continue
    return None


def _get_series(series_id: str) -> Optional[float]:
    """Return latest value for a FRED series, with file cache."""
    if series_id in _CACHE:
        return _CACHE[series_id].get("value")

    p = _cache_path(series_id)
    if not _cache_is_stale(series_id):
        try:
            stored = json.loads(p.read_text())
            _CACHE[series_id] = stored
            return stored.get("value")
        except Exception:
            pass

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    value = _fetch_series_latest(series_id)
    stored = {"series_id": series_id, "value": value}
    if value is not None:
        p.write_text(json.dumps(stored))
    _CACHE[series_id] = stored
    return value


def get_macro_indicators(zipcode: str) -> Optional[dict]:
    """
    Return macro economic context for the ZIP's state, or None if all fetches fail.

    Result dict shape:
      {
        "mortgage_rate_30yr":  6.82,     # %
        "state_unemployment":  3.7,      # % for ZIP's state
        "case_shiller_index":  320.4,    # national home price index
        "housing_starts_k":    1423.0,   # thousands of units (national)
        "state_code":          "MA",
      }
    """
    zipcode = str(zipcode).strip()[:5]
    cache_key = f"zip_{zipcode}"

    if cache_key in _CACHE:
        return _CACHE[cache_key].get("result")

    try:
        row = _nomi.query_postal_code(zipcode)
        state_code = str(row.get("state_code", "")).strip().upper()
        if not state_code or state_code.lower() == "nan":
            return None
    except Exception as exc:
        logger.warning("FRED: pgeocode failed for ZIP %s: %s", zipcode, exc)
        return None

    state_ur_series = _STATE_UR_SERIES.get(state_code)

    mortgage = _get_series("MORTGAGE30US")
    state_ur = _get_series(state_ur_series) if state_ur_series else None
    cs_index  = _get_series("CSUSHPISA")
    housing   = _get_series("HOUST")

    if all(v is None for v in [mortgage, state_ur, cs_index, housing]):
        return None

    result = {
        "mortgage_rate_30yr":  round(mortgage, 2) if mortgage is not None else None,
        "state_unemployment":  round(state_ur, 1) if state_ur is not None else None,
        "case_shiller_index":  round(cs_index, 1) if cs_index is not None else None,
        "housing_starts_k":    round(housing, 0) if housing is not None else None,
        "state_code":          state_code,
    }
    _CACHE[cache_key] = {"result": result}
    return result
