#!/usr/bin/env python3
"""
Task 0 - data quality flagging for listings.

⚠️ Every time data ingestion/scrape updates listings, rerun this script to refresh flags.

Priority order (highest first):
1) no_interior_photos
2) bad_sqft
3) rental_leakage
4) realtor_orphan
5) cross_period
6) active_only
7) list_eq_sold
8) clean

Flag semantics:
- Hard exclude from model training: no_interior_photos, bad_sqft, rental_leakage, realtor_orphan
- Informational metadata only: cross_period, active_only, list_eq_sold

Usage:
    cd /Users/jimmy20020528/Desktop/Edensign/bi
    source .venv/bin/activate
    python scripts/clean_outliers.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import asyncpg

_scripts = Path(__file__).resolve().parent.parent  # scripts/ root
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from db_dsn import get_db_dsn  # noqa: E402


FLAG_SQL = """
ALTER TABLE listings
ADD COLUMN IF NOT EXISTS data_quality_flag TEXT DEFAULT 'clean';

UPDATE listings l
SET data_quality_flag = CASE
  -- 1) no_interior_photos: >=80% photo URLs are Redfin public-record fallback media
  WHEN COALESCE((
    SELECT
      CASE
        WHEN COUNT(*) = 0 THEN 0
        ELSE SUM(CASE WHEN elem LIKE '%system_files/media/%' THEN 1 ELSE 0 END)::float / COUNT(*)
      END
    FROM jsonb_array_elements_text(COALESCE(l.photo_urls, '[]'::jsonb)) AS elem
  ), 0) >= 0.8 THEN 'no_interior_photos'

  -- 2) bad_sqft
  WHEN (l.sqft IS NOT NULL AND (l.sqft < 200 OR l.sqft > 10000)) THEN 'bad_sqft'

  -- 3) rental_leakage
  WHEN (
    (l.list_price IS NOT NULL AND l.list_price < 50000)
    OR (l.sold_price IS NOT NULL AND l.sold_price < 50000)
    OR (l.sold_price IS NOT NULL AND l.sqft IS NOT NULL AND l.sqft > 0
        AND (l.sold_price::numeric / l.sqft) < 100)
  ) THEN 'rental_leakage'

  -- 4) realtor_orphan
  WHEN (
    l.source = 'realtor'
    AND l.canonical_id IS NULL
    AND l.list_price IS NULL
  ) THEN 'realtor_orphan'

  -- 5) cross_period
  WHEN (
    l.listed_date IS NOT NULL
    AND l.sold_date IS NOT NULL
    AND l.listed_date > l.sold_date
  ) THEN 'cross_period'

  -- 6) active_only
  WHEN l.sold_date IS NULL THEN 'active_only'

  -- 7) list_eq_sold (strict GIS-bug signature: listed_date missing)
  WHEN (
    l.list_price IS NOT NULL
    AND l.sold_price IS NOT NULL
    AND ABS(l.list_price - l.sold_price) < 1
    AND l.listed_date IS NULL
  ) THEN 'list_eq_sold'

  ELSE 'clean'
END;
"""


async def main() -> None:
    conn = await asyncpg.connect(get_db_dsn())
    try:
        await conn.execute(FLAG_SQL)
        print("✅ data_quality_flag updated.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

