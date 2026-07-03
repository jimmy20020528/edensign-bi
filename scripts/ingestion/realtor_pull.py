"""
Edensign BI — Realtor.com 数据 Pull(via HomeHarvest)
==================================================
用 HomeHarvest 库(GitHub: ZacharyHampton/HomeHarvest)抓 realtor.com sold listings,
跟 Redfin (MLS-PIN base) union 后用 mls_id 跨源 dedup。

跟 Redfin 的关系:
  - Redfin 主要走 MLS-PIN (Boston 地区 MLS),broker 授权范围内
  - Realtor.com 更广 (Realtor.com 自家 + 多 MLS + 公共记录),通常多 30-50% 不重叠
  - 两边 mls_id (e.g. 73473365) 可以直接匹配同一套房 → 完美 dedup

不需要 API key,不需要付费,HomeHarvest 内部已经逆向了 realtor.com 内部 API。
realtor.com 用 Kasada 反爬,但 HomeHarvest 走 mobile app 的 GraphQL 端点,Kasada 不挡。

用法:
    cd /Users/jimmy20020528/Desktop/Edensign/bi
    source .venv/bin/activate

    # smoke test 单 ZIP 30 天
    python scripts/realtor_pull.py --zip 02135 --past-days 30

    # 全集(02134 + 02135 × 365 天 sold)
    python scripts/realtor_pull.py --all

    # 也抓 active(默认只 sold)
    python scripts/realtor_pull.py --all --include-active
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg
import pandas as pd
from homeharvest import scrape_property

_scripts = Path(__file__).resolve().parent.parent  # scripts/ root
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from db_dsn import get_db_dsn  # noqa: E402

# ════════════════════════════════════════════
# 配置
# ════════════════════════════════════════════

DEFAULT_ZIPS = ["02134", "02135"]
DEFAULT_PAST_DAYS = 365  # George 限定的训练范围

# HomeHarvest 'style' 字段 → 我们 schema 的 property_type
PROPERTY_TYPE_MAP = {
    "CONDOS": "Condo",
    "CONDO": "Condo",
    "SINGLE_FAMILY": "Single Family",
    "SINGLE FAMILY": "Single Family",
    "TOWNHOMES": "Townhouse",
    "TOWNHOUSE": "Townhouse",
    "MULTI_FAMILY": "Multi-Family",
    "MULTI FAMILY": "Multi-Family",
    "DUPLEX": "Multi-Family",
    "TRIPLEX": "Multi-Family",
    "APARTMENT": "Apartment",
    "LAND": "Land",
    "MOBILE": "Other",
    "FARM": "Other",
    "OTHER": "Other",
    "COOPERATIVE": "Condo",
    "CO_OP": "Condo",
}


def _to_int(val) -> int | None:
    if val is None or pd.isna(val):
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _to_float(val) -> float | None:
    if val is None or pd.isna(val):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _to_iso_date(val) -> str | None:
    """HomeHarvest 日期可能是 pd.Timestamp / str / None"""
    if val is None or pd.isna(val):
        return None
    if isinstance(val, str):
        return val[:10]
    try:
        return val.strftime("%Y-%m-%d")
    except AttributeError:
        return None


def _normalize_baths(full_baths, half_baths) -> float | None:
    """0.5 baths/half = half"""
    f = _to_int(full_baths) or 0
    h = _to_int(half_baths) or 0
    if f == 0 and h == 0:
        return None
    return float(f) + 0.5 * h


def _photos_to_list(primary_photo, alt_photos) -> list[str]:
    """合并 primary + alt,去重 + 截 15 张"""
    seen: set[str] = set()
    out: list[str] = []
    for url in [primary_photo]:
        if isinstance(url, str) and url.startswith("http") and url not in seen:
            seen.add(url)
            out.append(url)
    if isinstance(alt_photos, str):
        # alt_photos 字段是 ", " 分隔的字符串
        for url in alt_photos.split(", "):
            url = url.strip()
            if url.startswith("http") and url not in seen:
                seen.add(url)
                out.append(url)
                if len(out) >= 15:
                    break
    return out


# ════════════════════════════════════════════
# 解析 + 写库
# ════════════════════════════════════════════

def parse_row(row: pd.Series, source: str) -> dict | None:
    property_id = row.get("property_id")
    if not property_id or pd.isna(property_id):
        return None

    mls_id = row.get("mls_id")
    if pd.notna(mls_id):
        mls_id = str(int(float(mls_id))) if str(mls_id).replace(".", "").isdigit() else str(mls_id)

    style = str(row.get("style") or "").upper()

    listing_type = (row.get("status") or "").upper()
    if listing_type == "SOLD":
        sold_price = _to_int(row.get("sold_price") or row.get("last_sold_price"))
        list_price = _to_int(row.get("list_price"))
        sold_date = _to_iso_date(row.get("last_sold_date"))
        list_date = _to_iso_date(row.get("list_date"))
    else:
        sold_price = _to_int(row.get("last_sold_price"))
        list_price = _to_int(row.get("list_price"))
        sold_date = _to_iso_date(row.get("last_sold_date"))
        list_date = _to_iso_date(row.get("list_date"))

    return {
        "listing_id": f"rt_{property_id}",
        "address": row.get("formatted_address") or "",
        "city": row.get("city") or "Boston",
        "state": row.get("state") or "MA",
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
        "list_price": list_price,
        "sold_price": sold_price,
        "days_on_market": _to_int(row.get("days_on_mls")),
        "listed_date": list_date,
        "sold_date": sold_date,
        "photo_urls": _photos_to_list(row.get("primary_photo"), row.get("alt_photos")),
        "realtor_url": row.get("property_url"),
        # 用 mls_id 做跨源 dedup key,Phase 1 简单先存到 canonical_id 里
        # 同一房子 Redfin 是 'rf_<mls_id>',Realtor 是 'rt_<property_id>',
        # 但两边 mls_id 都一样,所以 canonical 用 mls_id 就行
        "_mls_id": mls_id if pd.notna(mls_id) else None,
    }


async def insert_listings(
    conn: asyncpg.Connection, parsed_list: list[dict]
) -> tuple[int, int, int]:
    """
    插入 + 跨源关联。
    返回 (inserted, skipped, dedup_marked) — dedup_marked 是检测到 Redfin 已有同 mls_id 的数量。
    """
    fields = [
        "listing_id", "address", "city", "state", "zipcode",
        "lat", "lng", "sqft", "bedrooms", "bathrooms",
        "lot_size", "year_built", "property_type", "hoa_fee",
        "list_price", "sold_price", "days_on_market",
        "listed_date", "sold_date", "photo_urls", "realtor_url",
    ]
    cols = ", ".join(fields)
    vals = ", ".join(f"${i+1}" for i in range(len(fields)))
    insert_sql = (
        f"INSERT INTO listings ({cols}, source) "
        f"VALUES ({vals}, 'realtor') "
        f"ON CONFLICT (listing_id) DO NOTHING"
    )

    inserted, skipped, dedup_marked = 0, 0, 0
    for p in parsed_list:
        # 1. 检测 Redfin 是否已有同 mls_id 的 listing → 用 canonical_id 关联
        mls_id = p.pop("_mls_id", None)
        canonical_id = None
        if mls_id:
            redfin_existing = await conn.fetchval(
                """
                SELECT listing_id FROM listings
                WHERE listing_id = $1
                  AND source = 'redfin'
                """,
                f"rf_{mls_id}",
            )
            if redfin_existing:
                # 把 Redfin 那条的 listing_id 作为 canonical
                canonical_id = redfin_existing
                dedup_marked += 1

        # 2. 插入 realtor 记录
        record = []
        for f in fields:
            v = p.get(f)
            if f in ("listed_date", "sold_date") and v:
                v = datetime.strptime(v, "%Y-%m-%d").date()
            if f == "photo_urls":
                v = json.dumps(v) if v else json.dumps([])
            record.append(v)
        try:
            res = await conn.execute(insert_sql, *record)
            if "INSERT 0 1" in res:
                inserted += 1
                # 3. 如果跟 Redfin 是同房,给两条都标 canonical_id
                if canonical_id:
                    await conn.execute(
                        "UPDATE listings SET canonical_id = $1 "
                        "WHERE listing_id IN ($2, $1)",
                        canonical_id, p["listing_id"],
                    )
            else:
                skipped += 1
        except Exception as e:
            print(f"    ⚠ insert 失败 [{p.get('listing_id')}]: {e}")
            skipped += 1
    return inserted, skipped, dedup_marked


# ════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════

def fetch_zip(zipcode: str, listing_type: str, past_days: int) -> pd.DataFrame:
    """单 ZIP 调 HomeHarvest"""
    print(f"  调 HomeHarvest: location={zipcode}, type={listing_type}, past_days={past_days}")
    df = scrape_property(
        location=zipcode,
        listing_type=listing_type,  # "sold" / "for_sale"
        past_days=past_days,
    )
    print(f"  返回 {len(df)} 行")
    return df


async def main(zips: list[str], past_days: int, include_active: bool) -> None:
    print(f"目标 ZIPs: {zips}")
    print(f"过去 N 天: {past_days}")
    print(f"包含 active: {include_active}\n")

    all_dfs: list[pd.DataFrame] = []

    for zipcode in zips:
        print(f"[ZIP {zipcode}]")
        # sold
        df_sold = fetch_zip(zipcode, "sold", past_days)
        if not df_sold.empty:
            all_dfs.append(df_sold)
        # active(可选)
        if include_active:
            df_active = fetch_zip(zipcode, "for_sale", past_days)
            if not df_active.empty:
                all_dfs.append(df_active)
        print()

    if not all_dfs:
        print("⚠ 没拿到任何 listings")
        return

    df_all = pd.concat(all_dfs, ignore_index=True)
    # 跨 ZIP / 跨 type 去重(同 property_id)
    df_all = df_all.drop_duplicates(subset=["property_id"]).reset_index(drop=True)
    print(f"=== 全部抓取 {len(df_all)} 条 unique (按 property_id 去重) ===\n")

    # 后过滤到目标 ZIP(防止 Realtor 带进相邻 ZIP 房)
    target = set(zips)
    df_filtered = df_all[df_all["zip_code"].isin(target)].reset_index(drop=True)
    print(f"过滤到 {sorted(target)} 后 {len(df_filtered)} 条")

    parsed: list[dict] = []
    for _, row in df_filtered.iterrows():
        p = parse_row(row, "realtor")
        if p:
            parsed.append(p)
    print(f"解析成功 {len(parsed)} / {len(df_filtered)}")

    if not parsed:
        return

    conn = await asyncpg.connect(get_db_dsn())
    inserted, skipped, dedup_marked = await insert_listings(conn, parsed)
    await conn.close()

    print(f"\n=== 完成 ===")
    print(f"  库里新插入: {inserted}")
    print(f"  跳过(已存在): {skipped}")
    print(f"  跨源 dedup 命中(跟 Redfin 同 mls_id): {dedup_marked}")
    print(
        f"\n下一步:\n"
        f"  1. detail_scrape 已不需要(HomeHarvest 已带 photos / list_price / DOM)\n"
        f"  2. 可选:跑 classify_styles --past-year-only 给新 listing 风格分类\n"
        f"  3. 重训 Stage A 模型(数据量翻倍,期望 MAPE 从 13% → 9-10%)"
    )


def _zips_for_city(city: str, state: str) -> list[str]:
    import zipcodes as _zc
    results = _zc.filter_by(city=city, state=state)
    if not results:
        raise SystemExit(f"No ZIP codes found for {city}, {state}")
    return [r["zip_code"] for r in results]


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    zip_group = p.add_mutually_exclusive_group()
    zip_group.add_argument("--zip", action="append", metavar="ZIPCODE", help="Target ZIP code(s)")
    zip_group.add_argument("--city", metavar="CITY", help="City name — all ZIPs looked up automatically")
    p.add_argument("--state", metavar="STATE", help="Two-letter state code, required with --city")
    p.add_argument(
        "--past-days", type=int, default=DEFAULT_PAST_DAYS,
        help=f"过去 N 天 sold(默认 {DEFAULT_PAST_DAYS})",
    )
    p.add_argument(
        "--include-active", action="store_true",
        help="也抓当前 active listings(默认只 sold)",
    )
    args = p.parse_args()

    if args.city:
        if not args.state:
            p.error("--state is required when using --city")
        zips = _zips_for_city(args.city, args.state)
        print(f"Resolved {args.city}, {args.state} → {len(zips)} ZIPs")
    elif args.zip:
        zips = args.zip
    else:
        zips = DEFAULT_ZIPS

    asyncio.run(main(zips, args.past_days, args.include_active))
