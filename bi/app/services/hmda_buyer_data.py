from __future__ import annotations

"""
HMDA Buyer Data — fetch real home-purchase buyer demographics from HMDA public records.

Data source: CFPB FFIEC Data Browser API (free, no API key needed).
  URL pattern: https://ffiec.cfpb.gov/v2/data-browser-api/view/csv
               ?counties={5-digit FIPS}&years=2024&actions_taken=1&loan_purposes=1

HMDA = Home Mortgage Disclosure Act. Every US mortgage application is reported
annually. This gives us WHO IS ACTUALLY BUYING homes (age, income, loan size)
for any US county — not just who lives there, but who is actively purchasing.

Key columns used (HMDA 2023 field names):
  - action_taken:  1 = loan originated (approved + closed)
  - loan_purpose:  1 = home purchase
  - income:        applicant income in $1000s (may be "NA" or "Exempt" — skip)
  - loan_amount:   loan amount in $1000s
  - applicant_age: categorical bracket, e.g. "25-34", "35-44", ">74"

Cache behavior:
  - File cached to bi/data/hmda_{county_fips}.csv
  - Re-downloaded if older than 60 days
  - In-memory dict cached in _COUNTY_CACHE keyed by county_fips
"""

import logging
import math
import time
from datetime import date
from pathlib import Path
from typing import Optional

import httpx
import pgeocode
import pandas as pd

logger = logging.getLogger(__name__)

BI_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BI_ROOT / "data"
CACHE_MAX_AGE_DAYS = 60

HMDA_BASE_URL = (
    "https://ffiec.cfpb.gov/v2/data-browser-api/view/csv"
    "?counties={county_fips}&years={year}&actions_taken=1&loan_purposes=1"
)


def _candidate_years() -> tuple[int, int]:
    """Try current_year-1 first, fall back to current_year-2."""
    y = date.today().year
    return y - 1, y - 2

# Age bracket ordering for "under 45" computation
AGE_BRACKETS = ["<25", "25-34", "35-44", "45-54", "55-64", "65-74", ">74"]
AGE_UNDER_45 = {"<25", "25-34", "35-44"}

# Module-level in-memory cache: county_fips -> profile dict
_COUNTY_CACHE: dict[str, dict] = {}

_nomi = pgeocode.Nominatim("us")

# USPS state abbreviation -> 2-digit state FIPS (zero-padded string)
# Source: ANSI INCITS 38:2009 (FIPS 5-2)
_STATE_FIPS: dict[str, str] = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06",
    "CO": "08", "CT": "09", "DE": "10", "DC": "11", "FL": "12",
    "GA": "13", "HI": "15", "ID": "16", "IL": "17", "IN": "18",
    "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23",
    "MD": "24", "MA": "25", "MI": "26", "MN": "27", "MS": "28",
    "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33",
    "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38",
    "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44",
    "SC": "45", "SD": "46", "TN": "47", "TX": "48", "UT": "49",
    "VT": "50", "VA": "51", "WA": "53", "WV": "54", "WI": "55",
    "WY": "56", "PR": "72", "VI": "78", "GU": "66", "AS": "60",
    "MP": "69",
}


def _cache_path(county_fips: str, year: int) -> Path:
    return DATA_DIR / f"hmda_{county_fips}_{year}.csv"


def _find_fresh_cache(county_fips: str) -> tuple[Optional[Path], Optional[int]]:
    """Return (path, year) if a non-stale cache exists for any candidate year."""
    for year in _candidate_years():
        p = _cache_path(county_fips, year)
        if p.exists() and (time.time() - p.stat().st_mtime) <= CACHE_MAX_AGE_DAYS * 86400:
            return p, year
    return None, None


def _download_county_csv(county_fips: str) -> tuple[bool, Optional[int]]:
    """Try candidate years in order. Returns (success, year_used)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for year in _candidate_years():
        url = HMDA_BASE_URL.format(county_fips=county_fips, year=year)
        path = _cache_path(county_fips, year)
        try:
            logger.info("Downloading HMDA %d data for county %s ...", year, county_fips)
            with httpx.Client(timeout=120.0, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
            content = resp.content
            first_line = content[:200].decode("utf-8", errors="replace").strip()
            if not first_line.startswith("activity_year"):
                logger.warning("HMDA %d response for county %s is not CSV", year, county_fips)
                continue
            path.write_bytes(content)
            logger.info("HMDA %d for county %s cached (%d bytes)", year, county_fips, len(content))
            return True, year
        except Exception as exc:
            logger.warning("HMDA %d download failed for county %s: %s", year, county_fips, exc)
    return False, None


_NAR_GENERATION: dict[str, str] = {
    "<25":   "Gen Z",
    "25-34": "Younger Millennial",
    "35-44": "Older Millennial",
    "45-54": "Gen X",
    "55-64": "Boomer",
    "65-74": "Boomer",
    ">74":   "Silent Generation",
}


def _derive_archetype(
    dominant_age: str,
    median_income_k: float,
    median_loan_k: float,
) -> str:
    """NAR generational label based on dominant buyer age group (NAR 2025 Generational Trends Report)."""
    return _NAR_GENERATION.get(dominant_age, "Mixed")


def _compute_profile(df: pd.DataFrame, county_fips: str) -> dict:
    """Aggregate a filtered HMDA dataframe into a buyer profile dict."""
    n_purchases = len(df)

    # Income: column 'income', values in $1000s; "NA"/"Exempt" already parsed as NaN
    income_series = pd.to_numeric(df["income"], errors="coerce").dropna()
    median_income_k = round(float(income_series.median()), 1) if not income_series.empty else 0.0

    # Loan amount: column 'loan_amount', in $1000s
    loan_series = pd.to_numeric(df["loan_amount"], errors="coerce").dropna()
    # HMDA 2023 reports loan_amount in dollars (not thousands) — divide by 1000
    median_loan_k = round(float(loan_series.median()) / 1000.0, 1) if not loan_series.empty else 0.0

    # Age: categorical brackets
    age_col = df["applicant_age"].dropna().astype(str)
    # Keep only recognized brackets
    age_valid = age_col[age_col.isin(AGE_BRACKETS)]
    if not age_valid.empty:
        dominant_age = age_valid.value_counts().idxmax()
        pct_under_45 = round(100.0 * age_valid.isin(AGE_UNDER_45).sum() / len(age_valid), 1)
    else:
        dominant_age = "35-44"  # safe fallback
        pct_under_45 = 0.0

    archetype = _derive_archetype(dominant_age, median_income_k, median_loan_k)

    return {
        "county_fips": county_fips,
        "n_purchases": n_purchases,
        "median_income_k": median_income_k,
        "median_loan_k": median_loan_k,
        "dominant_age_group": dominant_age,
        "pct_age_under_45": pct_under_45,
        "buyer_archetype": archetype,
    }


def _load_county_profile(county_fips: str, year: int) -> Optional[dict]:
    """Load CSV from disk, filter to purchase loans, and compute profile."""
    path = _cache_path(county_fips, year)
    try:
        # We already filter at API level (actions_taken=1&loan_purposes=1)
        # but read defensively in case a cached file was fetched differently
        df = pd.read_csv(
            path,
            usecols=["action_taken", "loan_purpose", "income", "loan_amount", "applicant_age"],
            na_values=["NA", "Exempt", ""],
            low_memory=True,
        )
        # Filter to originated home-purchase loans
        df = df[
            (df["action_taken"] == 1) &
            (df["loan_purpose"] == 1)
        ]
        if df.empty:
            logger.warning("No home-purchase loans found in HMDA data for county %s", county_fips)
            return None
        return _compute_profile(df, county_fips)
    except Exception as exc:
        logger.warning("Failed to parse HMDA CSV for county %s: %s", county_fips, exc)
        return None


def _fips_for_zip(zipcode: str) -> Optional[str]:
    """
    Use pgeocode to resolve a ZIP code to a 5-digit county FIPS string.
    Returns e.g. "25025" for ZIP 02135 (Boston, Suffolk County, MA).
    Returns None if lookup fails or data is missing.

    pgeocode note: the library provides:
      - state_code (USPS 2-letter abbrev, e.g. "MA")
      - county_code (numeric county FIPS within state, e.g. 25.0 for Suffolk)
    There is no state_fips field — we derive it from the _STATE_FIPS lookup table.
    """
    try:
        row = _nomi.query_postal_code(str(zipcode).strip())

        state_code = str(row.get("state_code", "")).strip().upper()
        county_code_raw = row.get("county_code")

        # Validate state_code
        if not state_code or state_code.lower() == "nan":
            return None

        state_fips = _STATE_FIPS.get(state_code)
        if state_fips is None:
            logger.warning("No FIPS mapping for state_code '%s' (ZIP %s)", state_code, zipcode)
            return None

        # county_code from pgeocode is a float (e.g. 25.0) or NaN
        if county_code_raw is None or (isinstance(county_code_raw, float) and math.isnan(county_code_raw)):
            return None

        county_code = str(int(county_code_raw)).zfill(3)
        return state_fips + county_code
    except Exception as exc:
        logger.warning("pgeocode lookup failed for ZIP %s: %s", zipcode, exc)
        return None


def get_buyer_profile(zipcode: str) -> Optional[dict]:
    """
    Return a buyer profile dict for the county containing this ZIP code,
    or None if data is unavailable for any reason.

    Result dict shape:
      {
        "county_fips": "25025",
        "n_purchases": 842,
        "median_income_k": 120.0,
        "median_loan_k": 580.0,
        "dominant_age_group": "35-44",
        "pct_age_under_45": 62.3,
        "buyer_archetype": "young_professional",
      }
    """
    county_fips = _fips_for_zip(zipcode)
    if county_fips is None:
        logger.info("No county FIPS found for ZIP %s — skipping HMDA lookup", zipcode)
        return None

    # Return cached result if available
    if county_fips in _COUNTY_CACHE:
        return _COUNTY_CACHE[county_fips]

    # Check for a fresh on-disk cache (any candidate year)
    cached_path, cached_year = _find_fresh_cache(county_fips)
    if cached_path is None:
        ok, cached_year = _download_county_csv(county_fips)
        if not ok or cached_year is None:
            return None

    profile = _load_county_profile(county_fips, cached_year)
    if profile is not None:
        _COUNTY_CACHE[county_fips] = profile
    return profile
