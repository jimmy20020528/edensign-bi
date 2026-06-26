from __future__ import annotations

"""
NCES School Data — public school quality metrics per ZIP via Urban Institute Education Data API.

Data source: Urban Institute Education Data Portal (wraps NCES CCD, free, no API key needed).
  Endpoint: https://educationdata.urban.org/api/v1/schools/ccd/directory/{year}/?fips={state_fips}

Quality proxy:
  - If FRL data available: enrollment-weighted free/reduced lunch rate → score 1-10 (10 = lowest poverty)
  - If FRL is null (CEP states like MA, NY): grade-coverage + school-count estimate

Cache: data/nces_state_{fips}.json per state, 180-day TTL
"""

import logging
import time
from pathlib import Path
from typing import Optional

import httpx
import pgeocode

logger = logging.getLogger(__name__)

BI_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BI_ROOT / "data"
CACHE_MAX_AGE_DAYS = 180
NCES_YEAR = 2022

BASE_URL = "https://educationdata.urban.org/api/v1/schools/ccd/directory/{year}/"

_nomi = pgeocode.Nominatim("us")

_STATE_FIPS: dict[str, int] = {
    "AL": 1, "AK": 2, "AZ": 4, "AR": 5, "CA": 6, "CO": 8, "CT": 9, "DE": 10,
    "DC": 11, "FL": 12, "GA": 13, "HI": 15, "ID": 16, "IL": 17, "IN": 18,
    "IA": 19, "KS": 20, "KY": 21, "LA": 22, "ME": 23, "MD": 24, "MA": 25,
    "MI": 26, "MN": 27, "MS": 28, "MO": 29, "MT": 30, "NE": 31, "NV": 32,
    "NH": 33, "NJ": 34, "NM": 35, "NY": 36, "NC": 37, "ND": 38, "OH": 39,
    "OK": 40, "OR": 41, "PA": 42, "RI": 44, "SC": 45, "SD": 46, "TN": 47,
    "TX": 48, "UT": 49, "VT": 50, "VA": 51, "WA": 53, "WV": 54, "WI": 55,
    "WY": 56,
}

# In-memory: state_fips (int) -> {zip (str) -> profile dict}
_STATE_CACHE: dict[int, dict[str, dict]] = {}

# Negative cache: states whose download failed with no usable local cache.
# The NCES host (educationdata.urban.org) sits behind Cloudflare and rejects
# datacenter IPs with a JS challenge (HTTP 403), so once a download fails we
# stop retrying for the process lifetime — avoids per-request latency and log
# spam. School data is simply omitted from the report when unavailable.
_FAILED_STATES: set[int] = set()


def _cache_path(state_fips: int) -> Path:
    return DATA_DIR / f"nces_state_{state_fips}.json"


def _cache_is_stale(state_fips: int) -> bool:
    path = _cache_path(state_fips)
    if not path.exists():
        return True
    return (time.time() - path.stat().st_mtime) > CACHE_MAX_AGE_DAYS * 86400


def _download_state(state_fips: int) -> Optional[list[dict]]:
    """Download all schools for a state. Handles pagination for large states."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    url = BASE_URL.format(year=NCES_YEAR)
    all_schools: list[dict] = []
    page = 1

    try:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            while True:
                resp = client.get(url, params={"fips": state_fips, "per_page": 2000, "page": page})
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                all_schools.extend(results)
                if not data.get("next"):
                    break
                page += 1

        logger.info("NCES: downloaded %d schools for state FIPS %d", len(all_schools), state_fips)
        return all_schools
    except Exception as exc:
        logger.warning("NCES download failed for state FIPS %d: %s", state_fips, exc)
        return None


def _compute_zip_profile(schools: list[dict]) -> Optional[dict]:
    """Aggregate school records for a single ZIP into a display-ready profile."""
    active = [
        s for s in schools
        if s.get("school_status") == 1 and (s.get("enrollment") or 0) > 0
    ]
    if not active:
        return None

    school_count = len(active)
    levels = {s.get("school_level") for s in active}
    has_elem   = 1 in levels
    has_middle = 2 in levels
    has_high   = 3 in levels

    # FRL-based quality score (most reliable, but null in CEP states)
    frl_schools = [
        s for s in active
        if s.get("free_or_reduced_price_lunch") is not None and (s.get("enrollment") or 0) > 0
    ]
    if frl_schools:
        total_enroll = sum(s["enrollment"] for s in frl_schools)
        total_frl    = sum(s["free_or_reduced_price_lunch"] for s in frl_schools)
        frl_rate     = total_frl / total_enroll if total_enroll > 0 else 0.5
        quality_score  = round(10.0 * (1.0 - frl_rate), 1)
        quality_basis  = "frl_rate"
    else:
        # Coverage-based estimate when FRL unavailable (CEP states)
        coverage = sum([has_elem, has_middle, has_high])
        quality_score  = round(min(10.0, 3.0 + coverage * 2.0 + min(school_count * 0.3, 3.0)), 1)
        quality_basis  = "coverage_estimate"

    return {
        "school_count": school_count,
        "has_elementary": has_elem,
        "has_middle_school": has_middle,
        "has_high_school": has_high,
        "quality_score": quality_score,
        "quality_basis": quality_basis,
        "data_year": NCES_YEAR,
    }


def _build_zip_index(schools: list[dict]) -> dict[str, dict]:
    """Group school records by 5-digit ZIP and compute per-ZIP profiles."""
    by_zip: dict[str, list[dict]] = {}
    for s in schools:
        z = str(s.get("zip_location") or "").strip()[:5]
        if z:
            by_zip.setdefault(z, []).append(s)

    profiles = {z: _compute_zip_profile(ss) for z, ss in by_zip.items()}
    return {z: p for z, p in profiles.items() if p is not None}


def _ensure_state_loaded(state_fips: int) -> bool:
    """Load state school data into memory, downloading if needed. Returns True on success."""
    import json

    if state_fips in _STATE_CACHE:
        return True

    # Already known to be unreachable with no local cache — skip silently.
    if state_fips in _FAILED_STATES:
        return False

    path = _cache_path(state_fips)

    if _cache_is_stale(state_fips):
        schools = _download_state(state_fips)
        if schools is None:
            if path.exists():
                pass  # fall through to load stale cache
            else:
                _FAILED_STATES.add(state_fips)
                logger.info(
                    "NCES: state FIPS %d unavailable (host blocked, no cache) — "
                    "school data will be omitted; not retrying this session.",
                    state_fips,
                )
                return False
        else:
            path.write_text(json.dumps(schools, separators=(",", ":")))

    try:
        schools = json.loads(path.read_text())
        _STATE_CACHE[state_fips] = _build_zip_index(schools)
        logger.info("NCES: loaded %d ZIPs for state FIPS %d", len(_STATE_CACHE[state_fips]), state_fips)
        return True
    except Exception as exc:
        logger.warning("NCES: failed to parse cache for state FIPS %d: %s", state_fips, exc)
        return False


def get_school_profile(zipcode: str) -> Optional[dict]:
    """
    Return school profile for the ZIP, or None if unavailable.

    Result dict shape:
      {
        "school_count": 7,
        "has_elementary": True,
        "has_middle_school": False,
        "has_high_school": True,
        "quality_score": 6.2,       # 1-10, 10 = best
        "quality_basis": "frl_rate" | "coverage_estimate",
        "data_year": 2022,
      }
    """
    zipcode = str(zipcode).strip()[:5]
    try:
        row = _nomi.query_postal_code(zipcode)
        state_code = str(row.get("state_code", "")).strip().upper()
        if not state_code or state_code.lower() == "nan":
            return None
        state_fips = _STATE_FIPS.get(state_code)
        if state_fips is None:
            return None
    except Exception as exc:
        logger.warning("NCES: pgeocode lookup failed for ZIP %s: %s", zipcode, exc)
        return None

    ok = _ensure_state_loaded(state_fips)
    if not ok:
        return None

    return _STATE_CACHE.get(state_fips, {}).get(zipcode)
