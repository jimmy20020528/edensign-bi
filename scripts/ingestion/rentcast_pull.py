"""
Edensign BI — Step 3 (RentCast): Pull sold properties by ZIP.

Usage:
    cd /Users/jimmy20020528/Desktop/Edensign/bi
    source .venv/bin/activate
    python scripts/rentcast_pull.py --zip 02134 --zip 02135 --days 365 --limit 500

Notes:
    - Requires RENTCAST_API_KEY in bi/.env
    - Uses official RentCast endpoint: /v1/properties
    - Writes into listings table with source='rentcast'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from dotenv import load_dotenv

_scripts = Path(__file__).resolve().parent.parent  # scripts/ root
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from db_dsn import get_db_dsn  # noqa: E402


load_dotenv(Path(__file__).resolve().parent.parent / ".env")

RENTCAST_API_KEY = os.environ.get("RENTCAST_API_KEY", "")
RENTCAST_BASE_URL = "https://api.rentcast.io/v1/properties"


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_date(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, str):
        # RentCast often returns ISO datetime strings.
        return value[:10]
    return None


def _collect_photo_urls(record: dict[str, Any]) -> list[str]:
    """
    RentCast fields can vary by plan/endpoint version.
    Collect common photo fields and fallback to listing history.
    """
    candidates: list[str] = []

    def add_many(maybe_urls: Any) -> None:
        if isinstance(maybe_urls, list):
            for u in maybe_urls:
                if isinstance(u, str) and u.startswith("http"):
                    candidates.append(u)

    add_many(record.get("photos"))
    add_many(record.get("images"))
    add_many(record.get("photoUrls"))

    history = record.get("history")
    if isinstance(history, dict):
        entries = history.get("sale") or history.get("listings") or history.get("events") or []
        if isinstance(entries, list):
            for item in entries:
                if not isinstance(item, dict):
                    continue
                add_many(item.get("photos"))
                add_many(item.get("images"))
                add_many(item.get("photoUrls"))

    # De-dup while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for u in candidates:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped[:15]


def parse_rentcast_record(row: dict[str, Any]) -> dict[str, Any] | None:
    property_id = row.get("id") or row.get("propertyId")
    if not property_id:
        return None

    street = row.get("formattedAddress") or row.get("addressLine1") or row.get("address") or ""
    city = row.get("city") or "Unknown"
    state = row.get("state") or "MA"
    zipcode = row.get("zipCode") or row.get("zip") or ""
    address = street if street else f"{city}, {state} {zipcode}"

    last_sale_price = row.get("lastSalePrice")
    if last_sale_price is None:
        # fallback if only history has sale values
        history = row.get("history")
        if isinstance(history, dict):
            sale_hist = history.get("sale")
            if isinstance(sale_hist, list) and sale_hist:
                last = sale_hist[-1]
                if isinstance(last, dict):
                    last_sale_price = last.get("price")

    listed_date = _to_date(row.get("listedDate"))
    sold_date = _to_date(row.get("lastSaleDate") or row.get("soldDate"))

    photos = _collect_photo_urls(row)

    return {
        "listing_id": f"rc_{property_id}",
        "address": address,
        "city": city,
        "state": state,
        "zipcode": zipcode,
        "lat": row.get("latitude"),
        "lng": row.get("longitude"),
        "sqft": _to_int(row.get("squareFootage")),
        "bedrooms": _to_int(row.get("bedrooms")),
        "bathrooms": row.get("bathrooms"),
        "lot_size": _to_int(row.get("lotSize")),
        "year_built": _to_int(row.get("yearBuilt")),
        "property_type": row.get("propertyType"),
        "hoa_fee": _to_int((row.get("hoa") or {}).get("fee") if isinstance(row.get("hoa"), dict) else None),
        "parking": None,
        "list_price": _to_int(row.get("price")),
        "sold_price": _to_int(last_sale_price),
        "days_on_market": _to_int(row.get("daysOnMarket")),
        "listed_date": listed_date,
        "sold_date": sold_date,
        "photo_urls": json.dumps(photos),
        "source": "rentcast",
    }


async def fetch_by_zip(
    client: httpx.AsyncClient,
    zipcode: str,
    days: int,
    limit: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = 0
    page_size = min(limit, 500)

    while True:
        params = {
            "zipCode": zipcode,
            "saleDateRange": f"*:{days}",
            "limit": page_size,
            "offset": offset,
        }
        resp = await client.get(RENTCAST_BASE_URL, params=params)
        resp.raise_for_status()
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            break
        out.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size
        if len(out) >= limit:
            break
    return out[:limit]


async def insert_listings(rows: list[dict[str, Any]]) -> tuple[int, int]:
    if not rows:
        return 0, 0

    conn = await asyncpg.connect(get_db_dsn())
    fields = [
        "listing_id",
        "address",
        "city",
        "state",
        "zipcode",
        "lat",
        "lng",
        "sqft",
        "bedrooms",
        "bathrooms",
        "lot_size",
        "year_built",
        "property_type",
        "hoa_fee",
        "parking",
        "list_price",
        "sold_price",
        "days_on_market",
        "listed_date",
        "sold_date",
        "photo_urls",
        "source",
    ]
    cols = ", ".join(fields)
    vals = ", ".join(f"${i+1}" for i in range(len(fields)))
    sql = f"""
        INSERT INTO listings ({cols})
        VALUES ({vals})
        ON CONFLICT (listing_id) DO NOTHING
    """

    inserted, skipped = 0, 0
    for row in rows:
        record: list[Any] = []
        for f in fields:
            v = row.get(f)
            if f in ("listed_date", "sold_date") and v:
                v = datetime.strptime(v, "%Y-%m-%d").date()
            record.append(v)
        try:
            result = await conn.execute(sql, *record)
            if "INSERT 0 1" in result:
                inserted += 1
            else:
                skipped += 1
        except Exception:
            skipped += 1

    await conn.close()
    return inserted, skipped


async def main(zipcodes: list[str], days: int, limit: int) -> None:
    if not RENTCAST_API_KEY or RENTCAST_API_KEY.startswith("your_"):
        raise SystemExit("❌ RENTCAST_API_KEY 未配置，请先在 bi/.env 填入有效 key。")

    headers = {
        "Accept": "application/json",
        "X-Api-Key": RENTCAST_API_KEY,
    }
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        raw_all: list[dict[str, Any]] = []
        for z in zipcodes:
            print(f"\n[ZIP {z}] fetching sold properties...")
            rows = await fetch_by_zip(client, z, days=days, limit=limit)
            print(f"  fetched {len(rows)} rows")
            raw_all.extend(rows)

    # parse and dedupe by listing_id
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in raw_all:
        row = parse_rentcast_record(r)
        if not row:
            continue
        lid = row["listing_id"]
        if lid in seen:
            continue
        seen.add(lid)
        parsed.append(row)

    print(f"\nParsed {len(parsed)} unique listings")
    inserted, skipped = await insert_listings(parsed)
    print(f"Inserted {inserted}, skipped {skipped}")

    with_photos = sum(1 for r in parsed if json.loads(r["photo_urls"]))
    print(f"Listings with photos: {with_photos}/{len(parsed)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", dest="zipcodes", action="append", required=True, help="ZIP code (repeatable)")
    parser.add_argument("--days", type=int, default=365, help="Lookback saleDateRange in days")
    parser.add_argument("--limit", type=int, default=500, help="Max rows per ZIP")
    args = parser.parse_args()
    asyncio.run(main(args.zipcodes, args.days, args.limit))

