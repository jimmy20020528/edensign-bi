#!/usr/bin/env python3
"""
Edensign BI — Combined Scraper (Redfin + Realtor.com)
=====================================================
Runs both scrapers in one command, deduplicates by MLS ID across sources,
and writes unique listings to PostgreSQL.

Usage:
    python scripts/ingestion/scraper.py --city Boston --state MA
    python scripts/ingestion/scraper.py --city Chicago --state IL --type all
    python scripts/ingestion/scraper.py --zip 02135 02134 --type sold

Listing types:
    sold        Redfin sold + Realtor sold (default — used for BI model training)
    for_sale    Redfin active + Realtor for_sale
    rent        Realtor for_rent only (Redfin rentals not scraped)
    all         sold + for_sale + rent

Time window (default):
    Jan 1 of previous calendar year → today.
    Override with --past-days if needed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pandas as pd
import pgeocode

_scripts = Path(__file__).resolve().parent.parent  # scripts/ root
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from db_dsn import get_db_dsn  # noqa: E402


# ══════════════════════════════════════════════════════════════
# Shared constants
# ══════════════════════════════════════════════════════════════

def _default_past_days() -> int:
    """Days from Jan 1 of the previous calendar year to today.
    In 2026 → pulls from 2025-01-01. In 2027 → pulls from 2026-01-01."""
    today = date.today()
    start = date(today.year - 1, 1, 1)
    return (today - start).days


# Redfin GIS
NUM_HOMES_PER_PAGE = 350
SLEEP_BETWEEN_REQUESTS = 2.0
# All possible time windows; filtered at runtime to <= past_days
_ALL_TIME_WINDOWS = [30, 90, 180, 365, 730, 1095]
ORDS = ["days-on-redfin-asc", "price-asc", "price-desc"]

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Realtor.com (HomeHarvest)
PROPERTY_TYPE_MAP = {
    "CONDOS": "Condo", "CONDO": "Condo",
    "SINGLE_FAMILY": "Single Family", "SINGLE FAMILY": "Single Family",
    "TOWNHOMES": "Townhouse", "TOWNHOUSE": "Townhouse",
    "MULTI_FAMILY": "Multi-Family", "MULTI FAMILY": "Multi-Family",
    "DUPLEX": "Multi-Family", "TRIPLEX": "Multi-Family",
    "APARTMENT": "Apartment", "LAND": "Land",
    "MOBILE": "Other", "FARM": "Other", "OTHER": "Other",
    "COOPERATIVE": "Condo", "CO_OP": "Condo",
}


# ══════════════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════════════

_nomi = pgeocode.Nominatim("us")


def _zips_for_city(city: str, state: str) -> list[str]:
    import zipcodes as _zc
    results = _zc.filter_by(city=city, state=state)
    if not results:
        raise SystemExit(f"No ZIP codes found for {city}, {state}")
    return [r["zip_code"] for r in results]


def _bbox_for_zip(zipcode: str, delta: float = 0.10) -> dict:
    row = _nomi.query_postal_code(zipcode)
    if row is None or (hasattr(row, "isna") and row.isna().get("latitude", False)):
        raise ValueError(f"pgeocode cannot resolve ZIP {zipcode!r}")
    lat, lon = float(row["latitude"]), float(row["longitude"])
    return {"south": lat - delta, "north": lat + delta,
            "west": lon - delta, "east": lon + delta}


def _market_for_zip(zipcode: str) -> str:
    row = _nomi.query_postal_code(zipcode)
    city = str(row.get("place_name", "")).strip().lower().replace(" ", "-")
    return city or "us"


def _bounds_to_poly(bounds: dict) -> str:
    s, w, n, e = bounds["south"], bounds["west"], bounds["north"], bounds["east"]
    points = [f"{w} {s}", f"{w} {n}", f"{e} {n}", f"{e} {s}", f"{w} {s}"]
    return ",".join(points)


def _to_int(val) -> int | None:
    if val is None:
        return None
    try:
        if hasattr(val, "__class__") and val.__class__.__name__ in ("float", "int", "int64", "float64"):
            if pd.isna(val):
                return None
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _to_float(val) -> float | None:
    if val is None:
        return None
    try:
        if hasattr(val, "__class__") and val.__class__.__name__ in ("float", "float64"):
            if pd.isna(val):
                return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _to_iso_date(val) -> str | None:
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, str):
        return val[:10]
    try:
        return val.strftime("%Y-%m-%d")
    except AttributeError:
        return None


def _nested(d: dict, *keys):
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k)
        else:
            return None
    return d


def _build_redfin_headers(user_agent: str) -> dict[str, str]:
    is_win = "Windows" in user_agent
    is_linux = "Linux" in user_agent and "Macintosh" not in user_agent
    if is_win:
        platform = '"Windows"'
    elif is_linux:
        platform = '"Linux"'
    else:
        platform = '"macOS"'
    return {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.redfin.com/",
        "Sec-Ch-Ua": '"Chromium";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": platform,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }


def _strip_redfin_prefix(text: str) -> str:
    idx = text.find('{"')
    if idx == -1:
        idx = text.find('{')
    return text[idx:] if idx >= 0 else text


# ══════════════════════════════════════════════════════════════
# Redfin GIS scraper
# ══════════════════════════════════════════════════════════════

async def _redfin_one_combo(
    client: httpx.AsyncClient,
    poly: str,
    sold_within_days: int,
    ord_value: str,
    market: str,
    status: int,
) -> tuple[list[dict], str]:
    ua = random.choice(USER_AGENTS)
    headers = _build_redfin_headers(ua)
    params: dict[str, Any] = {
        "al": 1,
        "isRentals": "false",
        "market": market,
        "num_homes": NUM_HOMES_PER_PAGE,
        "ord": ord_value,
        "page_number": 1,
        "poly": poly,
        "status": status,
        "uipt": "1,2,3,4,5,6,7,8",
        "v": 8,
    }
    if status == 9:
        params["sold_within_days"] = sold_within_days

    try:
        resp = await client.get(
            "https://www.redfin.com/stingray/api/gis",
            params=params,
            headers=headers,
        )
        resp.raise_for_status()
    except Exception as e:
        return [], f"request failed: {e}"

    try:
        data = json.loads(_strip_redfin_prefix(resp.text))
    except json.JSONDecodeError as e:
        return [], f"JSON parse failed: {e}"

    homes = data.get("payload", {}).get("homes") or []
    if not homes:
        err = data.get("payload", {}).get("errorMessage") or data.get("errorMessage") or ""
        return [], f"empty (errorMessage={err!r})"
    return homes, ""


async def scrape_redfin(
    target_zips: list[str],
    market: str,
    listing_type: str,  # "sold" | "for_sale"
    past_days: int,
) -> list[dict]:
    """
    Scrape Redfin GIS API.
    - sold: multi-combo time/sort sweep (windows capped at past_days) to break the 350-listing cap
    - for_sale: single query (status=1)
    Returns raw home dicts (not yet parsed).
    """
    bbox = _bbox_for_zip(target_zips[0])
    poly = _bounds_to_poly(bbox)
    raw_homes: list[dict] = []
    seen_ids: set[str] = set()

    client_kwargs: dict[str, Any] = {"timeout": 30.0, "follow_redirects": True}

    status = 9 if listing_type == "sold" else 1
    # Only use time windows that fit inside the requested past_days window
    active_windows = [d for d in _ALL_TIME_WINDOWS if d <= past_days] or [past_days]
    combos = [(d, o) for d in active_windows for o in ORDS] if listing_type == "sold" else [(past_days, "days-on-redfin-asc")]

    print(f"\n[Redfin] type={listing_type}  ZIPs={target_zips}  market={market!r}  combos={len(combos)}")

    async with httpx.AsyncClient(**client_kwargs) as client:
        for i, (days, ord_val) in enumerate(combos, start=1):
            homes, err = await _redfin_one_combo(client, poly, days, ord_val, market, status)
            new = 0
            for h in homes:
                pid = str(h.get("propertyId") or "")
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    raw_homes.append(h)
                    new += 1

            label = f"days={days:>4}, ord={ord_val:<22}"
            print(f"  [{i:>2}/{len(combos)}] {label} → {err or f'{len(homes)} returned, {new} new'} (total {len(raw_homes)})")

            if listing_type == "sold":
                await asyncio.sleep(SLEEP_BETWEEN_REQUESTS + random.uniform(-0.5, 1.5))

    target = set(target_zips)
    filtered = [h for h in raw_homes if h.get("zip") in target]
    print(f"  After ZIP filter ({sorted(target)}): {len(filtered)} / {len(raw_homes)}")
    return filtered


def parse_redfin_listing(home: dict) -> dict | None:
    try:
        mls_id = _nested(home, "mlsId", "value")
        prop_id = _nested(home, "propertyId")
        raw_id = str(mls_id or prop_id or "")
        if not raw_id:
            return None

        lat_long = _nested(home, "latLong", "value") or {}
        sold_ts = home.get("soldDate")
        sold_date = datetime.fromtimestamp(sold_ts / 1000).strftime("%Y-%m-%d") if sold_ts else None
        listed_ts = home.get("listingAddedDate")
        listed_date = datetime.fromtimestamp(listed_ts / 1000).strftime("%Y-%m-%d") if listed_ts else None

        dom = home.get("dom") or home.get("timeOnRedfin")
        if isinstance(dom, dict):
            dom = dom.get("value")

        photos: list[str] = []
        photo_data = _nested(home, "photos")
        if isinstance(photo_data, list):
            photos = photo_data[:10]
        elif isinstance(photo_data, dict):
            pv = photo_data.get("value", [])
            if isinstance(pv, list):
                photos = pv[:10]

        street = _nested(home, "streetLine", "value") or ""
        city = home.get("city") or ""
        state = home.get("state") or ""
        zipcode = home.get("zip") or ""

        prop_type_map = {1: "Single Family", 2: "Condo", 3: "Townhouse",
                         4: "Multi-Family", 5: "Land", 6: "Other", 13: "Condo"}

        is_rental = bool(home.get("isRental") or home.get("isRentals"))
        lt = "for_rent" if is_rental else ("sold" if sold_date else "for_sale")

        return {
            "listing_id": f"rf_{raw_id}",
            "_mls_id": str(mls_id) if mls_id else None,
            "address": f"{street}, {city}, {state} {zipcode}".strip(", "),
            "city": city,
            "state": state,
            "zipcode": zipcode,
            "lat": lat_long.get("latitude"),
            "lng": lat_long.get("longitude"),
            "sqft": _to_int(_nested(home, "sqFt", "value")),
            "bedrooms": _to_int(home.get("beds")),
            "bathrooms": home.get("baths"),
            "lot_size": _to_int(_nested(home, "lotSize", "value")),
            "year_built": _to_int(_nested(home, "yearBuilt", "value")),
            "property_type": prop_type_map.get(home.get("propertyType"), "Other"),
            "hoa_fee": _to_int(_nested(home, "hoa", "value")),
            "parking": None,
            "list_price": _to_int(_nested(home, "listPrice") or _nested(home, "price", "value")),
            "sold_price": _to_int(_nested(home, "price", "value")) if lt == "sold" else None,
            "days_on_market": _to_int(dom),
            "listed_date": listed_date,
            "sold_date": sold_date,
            "photo_urls": photos,
            "source": "redfin",
            "redfin_url": home.get("url", ""),
            "realtor_url": None,
            "listing_type": lt,
            "monthly_rent": _to_int(_nested(home, "price", "value")) if lt == "for_rent" else None,
        }
    except Exception as e:
        print(f"  ⚠ redfin parse error: {e}")
        return None


# ══════════════════════════════════════════════════════════════
# Realtor.com scraper (HomeHarvest)
# ══════════════════════════════════════════════════════════════

def _normalize_baths(full_baths, half_baths) -> float | None:
    f = _to_int(full_baths) or 0
    h = _to_int(half_baths) or 0
    if f == 0 and h == 0:
        return None
    return float(f) + 0.5 * h


def _photos_to_list(primary_photo, alt_photos) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    if isinstance(primary_photo, str) and primary_photo.startswith("http"):
        seen.add(primary_photo)
        out.append(primary_photo)
    if isinstance(alt_photos, str):
        for url in alt_photos.split(", "):
            url = url.strip()
            if url.startswith("http") and url not in seen:
                seen.add(url)
                out.append(url)
                if len(out) >= 15:
                    break
    return out


def scrape_realtor(
    zips: list[str],
    city: str | None,
    state: str | None,
    listing_type: str,  # "sold" | "for_sale" | "for_rent"
    past_days: int,
) -> list[dict]:
    """
    Scrape Realtor.com via HomeHarvest. Returns parsed listing dicts.
    listing_type maps to HomeHarvest types: sold, for_sale, for_rent.
    """
    from homeharvest import scrape_property

    hh_type_map = {"sold": "sold", "for_sale": "for_sale", "rent": "for_rent"}
    hh_type = hh_type_map.get(listing_type, listing_type)

    all_dfs: list[pd.DataFrame] = []
    print(f"\n[Realtor] type={listing_type} ({hh_type})  ZIPs={zips}")

    for zipcode in zips:
        print(f"  HomeHarvest: location={zipcode!r}, type={hh_type}, past_days={past_days}")
        try:
            df = scrape_property(location=zipcode, listing_type=hh_type, past_days=past_days)
            print(f"  → {len(df)} rows")
            if not df.empty:
                all_dfs.append(df)
        except Exception as e:
            print(f"  ⚠ HomeHarvest failed for {zipcode}: {e}")

    if not all_dfs:
        return []

    df_all = pd.concat(all_dfs, ignore_index=True)
    df_all = df_all.drop_duplicates(subset=["property_id"]).reset_index(drop=True)

    # Filter to target ZIPs only
    target = set(zips)
    df_filtered = df_all[df_all["zip_code"].isin(target)].reset_index(drop=True)
    print(f"  After ZIP filter: {len(df_filtered)} / {len(df_all)}")

    parsed: list[dict] = []
    for _, row in df_filtered.iterrows():
        p = _parse_realtor_row(row, hh_type)
        if p:
            parsed.append(p)
    print(f"  Parsed {len(parsed)} / {len(df_filtered)}")
    return parsed


def _parse_realtor_row(row: pd.Series, hh_type: str) -> dict | None:
    property_id = row.get("property_id")
    if not property_id or (hasattr(property_id, "__class__") and pd.isna(property_id)):
        return None

    mls_id_raw = row.get("mls_id")
    mls_id: str | None = None
    if mls_id_raw is not None and not (hasattr(mls_id_raw, "__class__") and pd.isna(mls_id_raw)):
        mls_id = str(int(float(mls_id_raw))) if str(mls_id_raw).replace(".", "").isdigit() else str(mls_id_raw)

    style = str(row.get("style") or "").upper()
    listing_status = (row.get("status") or "").upper()

    is_rent = hh_type == "for_rent"

    if listing_status == "SOLD":
        lt = "sold"
        sold_price = _to_int(row.get("sold_price") or row.get("last_sold_price"))
        list_price = _to_int(row.get("list_price"))
        sold_date = _to_iso_date(row.get("last_sold_date"))
        list_date = _to_iso_date(row.get("list_date"))
    elif is_rent:
        lt = "for_rent"
        sold_price = None
        list_price = _to_int(row.get("list_price"))
        sold_date = None
        list_date = _to_iso_date(row.get("list_date"))
    else:
        lt = "for_sale"
        sold_price = _to_int(row.get("last_sold_price"))
        list_price = _to_int(row.get("list_price"))
        sold_date = _to_iso_date(row.get("last_sold_date"))
        list_date = _to_iso_date(row.get("list_date"))

    return {
        "listing_id": f"rt_{property_id}",
        "_mls_id": mls_id,
        "address": row.get("formatted_address") or "",
        "city": row.get("city") or "",
        "state": row.get("state") or "",
        "zipcode": row.get("zip_code") or "",
        "lat": _to_float(row.get("latitude")),
        "lng": _to_float(row.get("longitude")),
        "sqft": _to_int(row.get("sqft")),
        "bedrooms": _to_int(row.get("beds")),
        "bathrooms": _normalize_baths(row.get("full_baths"), row.get("half_baths")),
        "lot_size": _to_int(row.get("lot_sqft")),
        "year_built": _to_int(row.get("year_built")),
        "property_type": PROPERTY_TYPE_MAP.get(style, "Other"),
        "hoa_fee": _to_int(row.get("hoa_fee")),
        "parking": None,
        "list_price": list_price,
        "sold_price": sold_price,
        "days_on_market": _to_int(row.get("days_on_mls")),
        "listed_date": list_date,
        "sold_date": sold_date,
        "photo_urls": _photos_to_list(row.get("primary_photo"), row.get("alt_photos")),
        "source": "realtor",
        "redfin_url": None,
        "realtor_url": row.get("property_url"),
        "listing_type": lt,
        "monthly_rent": list_price if is_rent else None,
    }


# ══════════════════════════════════════════════════════════════
# Cross-source dedup + DB write
# ══════════════════════════════════════════════════════════════

def _dedup_across_sources(
    redfin_listings: list[dict],
    realtor_listings: list[dict],
) -> tuple[list[dict], list[dict], int]:
    """
    Dedup by mls_id: when both sources have the same mls_id, we insert both
    (for data completeness) but mark them as canonical pairs.

    Returns (redfin_to_insert, realtor_to_insert, n_cross_source_pairs).
    The _mls_id key is kept in the dicts so insert_to_db can set canonical_id.
    """
    rf_by_mls: dict[str, str] = {}
    for listing in redfin_listings:
        mid = listing.get("_mls_id")
        if mid:
            rf_by_mls[mid] = listing["listing_id"]

    cross_pairs = 0
    for listing in realtor_listings:
        mid = listing.get("_mls_id")
        if mid and mid in rf_by_mls:
            listing["_canonical_rf_id"] = rf_by_mls[mid]
            cross_pairs += 1
        else:
            listing["_canonical_rf_id"] = None

    return redfin_listings, realtor_listings, cross_pairs


FIELDS = [
    "listing_id", "address", "city", "state", "zipcode",
    "lat", "lng", "sqft", "bedrooms", "bathrooms",
    "lot_size", "year_built", "property_type", "hoa_fee", "parking",
    "list_price", "sold_price", "days_on_market",
    "listed_date", "sold_date", "photo_urls", "source",
    "redfin_url", "realtor_url",
    "listing_type", "monthly_rent",
]


async def insert_to_db(
    conn: asyncpg.Connection,
    listings: list[dict],
    label: str,
) -> tuple[int, int]:
    """Insert listings, set canonical_id for cross-source pairs. Returns (inserted, skipped)."""
    cols = ", ".join(FIELDS)
    vals = ", ".join(f"${i + 1}" for i in range(len(FIELDS)))
    sql = (
        f"INSERT INTO listings ({cols}) VALUES ({vals}) "
        f"ON CONFLICT (listing_id) DO NOTHING"
    )

    inserted = skipped = 0
    for listing in listings:
        canonical_rf_id: str | None = listing.get("_canonical_rf_id")
        record = []
        for f in FIELDS:
            v = listing.get(f)
            if f in ("listed_date", "sold_date") and v:
                v = datetime.strptime(v, "%Y-%m-%d").date()
            if f == "photo_urls":
                v = json.dumps(v) if isinstance(v, list) else (v or json.dumps([]))
            record.append(v)

        try:
            res = await conn.execute(sql, *record)
            if "INSERT 0 1" in res:
                inserted += 1
                if canonical_rf_id:
                    await conn.execute(
                        "UPDATE listings SET canonical_id = $1 WHERE listing_id IN ($2, $1)",
                        canonical_rf_id, listing["listing_id"],
                    )
            else:
                skipped += 1
        except Exception as e:
            print(f"  ⚠ insert failed [{listing.get('listing_id')}]: {e}")
            skipped += 1

    print(f"  [{label}] inserted {inserted}, skipped {skipped} (duplicate or error)")
    return inserted, skipped


# ══════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════

async def run(
    zips: list[str],
    city: str | None,
    state: str | None,
    listing_types: list[str],
    past_days: int,
    skip_redfin: bool,
    skip_realtor: bool,
) -> None:
    market = _market_for_zip(zips[0])
    print(f"\nEdensign BI — Combined Scraper")
    print(f"ZIPs: {zips}  market: {market!r}")
    print(f"Types: {listing_types}  past_days: {past_days}")

    all_redfin: list[dict] = []
    all_realtor: list[dict] = []

    redfin_types = [t for t in listing_types if t in ("sold", "for_sale")]
    realtor_type_map = {"sold": "sold", "for_sale": "for_sale", "rent": "for_rent"}

    # Redfin scrape
    if not skip_redfin:
        for lt in redfin_types:
            homes = await scrape_redfin(zips, market, lt, past_days)
            for h in homes:
                parsed = parse_redfin_listing(h)
                if parsed:
                    all_redfin.append(parsed)
        print(f"\nRedfin total parsed: {len(all_redfin)}")

    # Realtor scrape
    if not skip_realtor:
        for lt in listing_types:
            hh_lt = realtor_type_map.get(lt, lt)
            result = scrape_realtor(zips, city, state, lt, past_days)
            all_realtor.extend(result)
        print(f"\nRealtor total parsed: {len(all_realtor)}")

    # Cross-source dedup
    all_redfin, all_realtor, n_pairs = _dedup_across_sources(all_redfin, all_realtor)
    print(f"\nCross-source MLS matches: {n_pairs} (both sources have the same listing)")

    # Write to DB
    conn = await asyncpg.connect(get_db_dsn())
    rf_ins = rf_skip = rt_ins = rt_skip = 0
    if all_redfin:
        rf_ins, rf_skip = await insert_to_db(conn, all_redfin, "Redfin")
    if all_realtor:
        rt_ins, rt_skip = await insert_to_db(conn, all_realtor, "Realtor")
    await conn.close()

    total_new = rf_ins + rt_ins
    print(f"\n{'=' * 50}")
    print(f"  Redfin:  inserted {rf_ins}, skipped {rf_skip}")
    print(f"  Realtor: inserted {rt_ins}, skipped {rt_skip}")
    print(f"  Cross-source pairs: {n_pairs}")
    print(f"  Total new listings: {total_new}")
    print(f"{'=' * 50}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Edensign BI — combined Redfin + Realtor.com scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    loc_group = ap.add_mutually_exclusive_group(required=True)
    loc_group.add_argument("--city", metavar="CITY", help="City name (e.g. Boston)")
    loc_group.add_argument("--zip", nargs="+", metavar="ZIPCODE", help="One or more ZIP codes")
    ap.add_argument("--state", metavar="STATE", help="Two-letter state code, required with --city")
    ap.add_argument(
        "--type",
        default="sold",
        choices=["sold", "for_sale", "rent", "all"],
        help="Listing type to scrape (default: sold)",
    )
    ap.add_argument(
        "--past-days", type=int, default=None,
        help="Days back to pull (default: Jan 1 of previous calendar year)",
    )
    ap.add_argument("--skip-redfin", action="store_true", help="Skip Redfin scrape")
    ap.add_argument("--skip-realtor", action="store_true", help="Skip Realtor.com scrape")
    args = ap.parse_args()

    if args.city and not args.state:
        ap.error("--state is required when using --city")

    if args.city:
        zips = _zips_for_city(args.city, args.state)
        print(f"Resolved {args.city}, {args.state} → {len(zips)} ZIPs")
    else:
        zips = args.zip

    past_days = args.past_days if args.past_days is not None else _default_past_days()
    today = date.today()
    start_date = today - timedelta(days=past_days)
    print(f"Scrape window: {start_date} → {today} ({past_days} days)")

    if args.type == "all":
        listing_types = ["sold", "for_sale", "rent"]
    else:
        listing_types = [args.type]

    asyncio.run(run(
        zips=zips,
        city=args.city,
        state=args.state,
        listing_types=listing_types,
        past_days=past_days,
        skip_redfin=args.skip_redfin,
        skip_realtor=args.skip_realtor,
    ))


if __name__ == "__main__":
    main()
