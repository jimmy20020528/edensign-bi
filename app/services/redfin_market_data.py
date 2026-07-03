from __future__ import annotations

"""
Redfin Market Data — fetch real median sale PPSF and median DOM for any US ZIP.

Data source: Redfin public market tracker TSV.gz (free, no API key needed).
  URL: https://redfin-public-data.s3.us-west-2.amazonaws.com/redfin_market_tracker/zip_code_market_tracker.tsv000.gz

Column notes (verified from header):
  - REGION:         "Zip Code: 12345"  (quoted string with prefix)
  - PROPERTY_TYPE:  "All Residential"  (quoted)
  - PERIOD_END:     "2024-03-31"       (quarterly end date, quoted)
  - MEDIAN_PPSF:    float or NA
  - MEDIAN_DOM:     float or NA
  - HOMES_SOLD:     int or NA

Cache behavior:
  - File cached to bi/data/redfin_zip_market.tsv.gz
  - Re-downloaded if older than 30 days
  - In-memory dict cached after first load (O(1) lookups)
"""

import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

BI_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_PATH = BI_ROOT / "data" / "redfin_zip_market.tsv.gz"
CACHE_MAX_AGE_DAYS = 30

DEFAULT_URL = (
    "https://redfin-public-data.s3.us-west-2.amazonaws.com"
    "/redfin_market_tracker/zip_code_market_tracker.tsv000.gz"
)

# In-memory cache: zipcode (str) -> {"median_psf": float, "median_dom": float, "homes_sold": int}
_ZIP_CACHE: dict[str, dict] = {}
_CACHE_LOADED = False
_LOAD_LOCK = threading.Lock()


def _cache_is_stale() -> bool:
    if not CACHE_PATH.exists():
        return True
    age = time.time() - CACHE_PATH.stat().st_mtime
    return age > CACHE_MAX_AGE_DAYS * 86400


def _download_file() -> bool:
    """Download Redfin TSV.gz to CACHE_PATH. Returns True on success."""
    url = os.environ.get("REDFIN_DATA_URL", DEFAULT_URL)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        logger.info("Downloading Redfin market data from %s ...", url)
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
        CACHE_PATH.write_bytes(resp.content)
        logger.info("Redfin data cached to %s (%d bytes)", CACHE_PATH, len(resp.content))
        return True
    except Exception as exc:
        logger.warning("Redfin download failed: %s", exc)
        return False


def _load_into_memory() -> None:
    """Parse CACHE_PATH and populate _ZIP_CACHE. Filters aggressively to keep memory low."""
    global _ZIP_CACHE, _CACHE_LOADED

    _ZIP_CACHE = {}

    try:
        # Read only the columns we need; quote_char strips surrounding double-quotes
        usecols = ["REGION", "PROPERTY_TYPE", "PERIOD_END", "MEDIAN_PPSF", "MEDIAN_DOM", "HOMES_SOLD"]
        chunks = pd.read_csv(
            CACHE_PATH,
            sep="\t",
            compression="gzip",
            usecols=usecols,
            na_values=["NA", ""],
            quotechar='"',
            chunksize=200_000,
            low_memory=True,
        )

        filtered_chunks = []
        for chunk in chunks:
            # Keep only "All Residential"
            sub = chunk[chunk["PROPERTY_TYPE"] == "All Residential"].copy()
            if not sub.empty:
                filtered_chunks.append(sub)

        if not filtered_chunks:
            logger.warning("No 'All Residential' rows found in Redfin data.")
            return

        df = pd.concat(filtered_chunks, ignore_index=True)

        # Parse period_end and keep only the most recent date
        df["PERIOD_END"] = pd.to_datetime(df["PERIOD_END"], errors="coerce")
        df = df.dropna(subset=["PERIOD_END"])
        most_recent = df["PERIOD_END"].max()
        df = df[df["PERIOD_END"] == most_recent]

        # Extract 5-digit ZIP from "Zip Code: 12345"
        df["zip"] = df["REGION"].str.extract(r"Zip Code:\s*(\d{5})")
        df = df.dropna(subset=["zip", "MEDIAN_PPSF"])
        dupes = df.duplicated(subset=["zip"]).sum()
        if dupes:
            logger.warning("Redfin: %d duplicate ZIP rows dropped (keeping last)", dupes)
            df = df.drop_duplicates(subset=["zip"], keep="last")

        # Build lookup — vectorized to avoid per-row Python overhead
        _ZIP_CACHE = {
            str(r["zip"]): {
                "median_psf": round(float(r["MEDIAN_PPSF"]), 2),
                "median_dom": round(float(r["MEDIAN_DOM"]), 1) if pd.notna(r["MEDIAN_DOM"]) else None,
                "homes_sold": int(r["HOMES_SOLD"]) if pd.notna(r["HOMES_SOLD"]) else 0,
            }
            for r in df.to_dict("records")
        }

        logger.info(
            "Redfin data loaded: %d ZIPs for period %s",
            len(_ZIP_CACHE),
            most_recent.date(),
        )

    except Exception as exc:
        logger.warning("Failed to load Redfin data into memory: %s", exc)
    finally:
        _CACHE_LOADED = True  # mark done (even on failure) so we don't retry endlessly


def _ensure_loaded() -> None:
    global _CACHE_LOADED
    if _CACHE_LOADED:
        return
    with _LOAD_LOCK:
        if _CACHE_LOADED:  # double-checked locking
            return
        if _cache_is_stale():
            ok = _download_file()
            if not ok and not CACHE_PATH.exists():
                _CACHE_LOADED = True  # give up, don't retry
                return
        _load_into_memory()


def get_zip_market_data(zipcode: str) -> Optional[dict]:
    """
    Return {"median_psf": float, "median_dom": float|None, "homes_sold": int} for the ZIP,
    or None if not found or data unavailable.
    """
    try:
        _ensure_loaded()
    except Exception as exc:
        logger.warning("get_zip_market_data failed to load: %s", exc)
        return None

    return _ZIP_CACHE.get(str(zipcode).strip())
