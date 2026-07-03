"""
Edensign BI — Redfin Sold Listings Scraper
==========================================
抓取任意美国 ZIP code 的已售房源数据,写入 PostgreSQL listings 表。

Redfin 没有官方 API,但网站内部用 /stingray/api/gis 端点加载数据。
我们直接请求这个端点,返回 JSON 格式的 listing 数据。

用法:
    python scripts/redfin_scrape.py --zip 02135
    python scripts/redfin_scrape.py --zip 02135 02134   (多个 ZIP)
    python scripts/redfin_scrape.py --zip 60614         (Chicago Lincoln Park)

注意: 不需要任何 API key
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
import asyncpg
import pgeocode

_scripts = Path(__file__).resolve().parent.parent  # scripts/ root
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from db_dsn import get_db_dsn  # noqa: E402

# Redfin GIS 单查询硬上限,服务端写死,翻页拿更多
NUM_HOMES_PER_PAGE = 350

# 单 ZIP 翻页安全上限,避免无限循环(一个 ZIP 一年通常 < 1000 条成交)
MAX_PAGES_PER_ZIP = 10

# 礼貌等待秒数,降低被 Redfin 风控概率
SLEEP_BETWEEN_REQUESTS = 2.0

# 突破 Redfin 350 单查询 cap 的策略:同 polygon 用不同 (sold_within_days, ord)
# 组合发请求,每个组合的"内部排序锚点"不同 → 返回的 350 不同 → 求并集
# 6 时段 × 3 排序 = 18 次组合,理论最多 6300 条,实际去重后 500-1500 条
TIME_WINDOWS = [30, 90, 180, 365, 730, 1095]  # 过去 N 天成交
ORDS = [
    "days-on-redfin-asc",   # 在 Redfin 上挂得久的优先
    "price-asc",            # 价格升序
    "price-desc",           # 价格降序
]

# 多个 User-Agent 轮换,降低被识别为同一脚本的概率
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

# Redfin 请求头 — 模拟正常浏览器,headers 太单薄会被 Cloudflare 直接 403
# 给 GIS API 用的请求头(JSON 接口)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.redfin.com/",
    "Sec-Ch-Ua": '"Chromium";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

# 给 HTML 页用的请求头(浏览器导航)
HTML_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": HEADERS["Sec-Ch-Ua"],
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


# ══════════════════════════════════════════════
# Redfin GIS API — Region 解析 + 分页抓取
# ══════════════════════════════════════════════

def _strip_redfin_prefix(text: str) -> str:
    """Redfin 内部端点返回会带 '{}&&' 前缀,去掉之后才是合法 JSON。"""
    idx = text.find('{"')
    if idx == -1:
        idx = text.find('{')
    return text[idx:] if idx >= 0 else text


_nomi = pgeocode.Nominatim("us")

# pgeocode = 离线 ZIP→坐标库,数据来自 GeoNames,不用联网不用 API key
def _bbox_for_zip(zipcode: str, delta: float = 0.10) -> dict:
    """
    给定 ZIP code 返回一个覆盖它周围的矩形 bounding box。
    delta = 0.10° ≈ 11 km,足够覆盖单个 ZIP + 相邻区域。
    Redfin 用这个 polygon 参数来框定搜索范围(必填,否则 Invalid arguments)。
    """
    row = _nomi.query_postal_code(zipcode)
    if row is None or (hasattr(row, "isna") and row.isna().get("latitude", False)):
        raise ValueError(f"pgeocode 无法解析 ZIP {zipcode!r}")
    lat, lon = float(row["latitude"]), float(row["longitude"])
    return {"south": lat - delta, "north": lat + delta,
            "west": lon - delta, "east": lon + delta}


def _market_for_zip(zipcode: str) -> str:
    """
    从 ZIP 推导 Redfin market slug(e.g. 'boston', 'chicago', 'los-angeles')。
    Redfin 用 market 做后端路由,不完全匹配时会 fallback,实测影响不大。
    """
    row = _nomi.query_postal_code(zipcode)
    city = str(row.get("place_name", "")).strip().lower().replace(" ", "-")
    return city or "us"


def _bounds_to_poly(bounds: dict) -> str:
    s, w, n, e = bounds["south"], bounds["west"], bounds["north"], bounds["east"]
    points = [f"{w} {s}", f"{w} {n}", f"{e} {n}", f"{e} {s}", f"{w} {s}"]
    return ",".join(points)


def _build_headers(user_agent: str) -> dict[str, str]:
    """每次请求用一组 UA + 配套 Sec-Ch-Ua,降低被聚合识别概率。"""
    is_mac = "Macintosh" in user_agent
    is_win = "Windows" in user_agent
    is_linux = "Linux" in user_agent and "Macintosh" not in user_agent
    if is_mac:
        platform = '"macOS"'
    elif is_win:
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


async def fetch_one_combo(
    client: httpx.AsyncClient,
    poly: str,
    sold_within_days: int,
    ord_value: str,
    market: str = "us",
) -> tuple[list[dict], str]:
    """单次 GIS 查询,返回 (homes, error_msg)。换 UA 模拟不同浏览器。"""
    ua = random.choice(USER_AGENTS)
    headers = _build_headers(ua)
    params = {
        "al": 1,
        "isRentals": "false",
        "market": market,
        "num_homes": NUM_HOMES_PER_PAGE,
        "ord": ord_value,
        "page_number": 1,
        "poly": poly,
        "sold_within_days": sold_within_days,
        "status": 9,
        "uipt": "1,2,3,4,5,6,7,8",
        "v": 8,
    }
    url = "https://www.redfin.com/stingray/api/gis"

    try:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
    except Exception as e:
        return [], f"请求失败: {e}"

    try:
        data = json.loads(_strip_redfin_prefix(resp.text))
    except json.JSONDecodeError as e:
        return [], f"JSON 解析失败: {e}"

    homes = data.get("payload", {}).get("homes") or []
    if not homes:
        err = data.get("payload", {}).get("errorMessage") or data.get(
            "errorMessage"
        ) or ""
        return [], f"empty (errorMessage={err!r})"
    return homes, ""


async def fetch_redfin_sold_listings(
    target_zips: list[str],
    market: str,
) -> list[dict]:
    """
    扩样核心:对同一 polygon,遍历所有 (sold_within_days, ord) 组合,
    每个组合返回的 350 集不同 → 求并集突破单查询 cap。
    target_zips: 抓完后按 ZIP 过滤
    market: Redfin 后端路由 slug,e.g. 'boston'
    """
    # 用第一个 ZIP 的坐标为中心生成 polygon,多个 ZIP 时它们通常邻近
    bbox = _bbox_for_zip(target_zips[0])
    poly = _bounds_to_poly(bbox)
    raw_homes: list[dict] = []
    seen_property_ids: set[str] = set()

    combos = [(d, o) for d in TIME_WINDOWS for o in ORDS]
    total_combos = len(combos)
    consecutive_zero = 0  # 连续多次 0 新增 → 已饱和

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for i, (days, ord_value) in enumerate(combos, start=1):
            label = f"days={days:>4}, ord={ord_value:<22}"
            homes, err = await fetch_one_combo(client, poly, days, ord_value, market=market)

            new = 0
            for h in homes:
                pid = str(h.get("propertyId") or "")
                if pid and pid not in seen_property_ids:
                    seen_property_ids.add(pid)
                    raw_homes.append(h)
                    new += 1

            status = err if err else f"返回 {len(homes)} 条, 新增 {new}"
            print(f"  [{i:>2}/{total_combos}] {label} → {status} (累计 {len(raw_homes)})")

            if new == 0 and not err:
                consecutive_zero += 1
            else:
                consecutive_zero = 0

            # 连续 4 次 0 新增 → 已饱和,后续 combo 也只是重复
            if consecutive_zero >= 4:
                print(f"  (连续 {consecutive_zero} 个 combo 0 新增,已饱和,提前停)")
                break

            # 随机抖动 1.5-3.5s,模拟人操作节奏
            await asyncio.sleep(SLEEP_BETWEEN_REQUESTS + random.uniform(-0.5, 1.5))

    target = set(target_zips)
    filtered = [h for h in raw_homes if h.get("zip") in target]
    print(
        f"\n  raw 跨 combo 去重后 {len(raw_homes)} 条,"
        f"过滤到 {sorted(target)} 后剩 {len(filtered)} 条"
    )
    return filtered


# ══════════════════════════════════════════════
# 解析 Redfin listing 数据
# ══════════════════════════════════════════════

from typing import Optional

def parse_listing(home: dict) -> Optional[dict]:
    """
    把 Redfin 的原始 home 对象转成我们的 listing 字段

    Redfin 返回的每个 home 大致结构:
    {
        "mlsId": {"value": "73123456"},
        "price": {"value": 850000},
        "beds": 3,
        "baths": 2.0,
        "sqFt": {"value": 1200},
        "latLong": {"value": {"latitude": 42.353, "longitude": -71.131}},
        "streetLine": {"value": "123 Harvard Ave"},
        "city": "Boston",
        "state": "MA",
        "zip": "02134",
        "soldDate": 1704067200000,     ← Unix timestamp (毫秒)
        "listingRemarks": "Beautiful...",
        "photos": {"value": ["url1", "url2", ...]},
        ...
    }
    """
    try:
        # 提取 listing ID (用 mlsId 或 propertyId 作为唯一标识)
        mls_id = _nested(home, "mlsId", "value")
        prop_id = _nested(home, "propertyId")
        listing_id = str(mls_id or prop_id or "")
        if not listing_id:
            return None

        # 经纬度
        lat_long = _nested(home, "latLong", "value") or {}
        lat = lat_long.get("latitude")
        lng = lat_long.get("longitude")

        # 价格
        sold_price = _nested(home, "price", "value")
        list_price = _nested(home, "listPrice") or _nested(home, "price", "value")

        # 日期 (Redfin 用毫秒级 Unix timestamp)
        sold_ts = home.get("soldDate")
        sold_date = None
        if sold_ts:
            # 毫秒 → 秒, 然后转日期
            sold_date = datetime.fromtimestamp(sold_ts / 1000).strftime("%Y-%m-%d")

        listed_ts = home.get("listingAddedDate")
        listed_date = None
        if listed_ts:
            listed_date = datetime.fromtimestamp(listed_ts / 1000).strftime("%Y-%m-%d")

        # DOM (days on market)
        dom = home.get("dom") or home.get("timeOnRedfin")
        if dom and isinstance(dom, dict):
            dom = dom.get("value")

        # 照片 URLs
        photos = []
        photo_data = _nested(home, "photos")
        if isinstance(photo_data, list):
            photos = photo_data[:10]  # 最多存10张
        elif isinstance(photo_data, dict):
            photo_val = photo_data.get("value", [])
            if isinstance(photo_val, list):
                photos = photo_val[:10]

        # 地址
        street = _nested(home, "streetLine", "value") or ""
        city = home.get("city") or ""
        state = home.get("state") or ""
        zipcode = home.get("zip") or ""
        address = f"{street}, {city}, {state} {zipcode}".strip(", ")

        # 房屋属性
        sqft = _nested(home, "sqFt", "value")
        beds = home.get("beds")
        baths = home.get("baths")
        lot_size = _nested(home, "lotSize", "value")
        year_built = _nested(home, "yearBuilt", "value")

        # 房屋类型映射
        prop_type_code = home.get("propertyType")
        prop_type_map = {
            1: "Single Family",
            2: "Condo",
            3: "Townhouse",
            4: "Multi-Family",
            5: "Land",
            6: "Other",
            13: "Condo",
        }
        property_type = prop_type_map.get(prop_type_code, "Other")

        # HOA
        hoa = _nested(home, "hoa", "value")

        return {
            "listing_id": f"rf_{listing_id}",  # rf_ 前缀标记来源是Redfin
            "address": address,
            "city": city,
            "state": state,
            "zipcode": zipcode,
            "lat": lat,
            "lng": lng,
            "sqft": _to_int(sqft),
            "bedrooms": _to_int(beds),
            "bathrooms": baths,
            "lot_size": _to_int(lot_size),
            "year_built": _to_int(year_built),
            "property_type": property_type,
            "hoa_fee": _to_int(hoa),
            "parking": None,  # Redfin GIS API 不直接返回parking
            "list_price": _to_int(list_price),
            "sold_price": _to_int(sold_price),
            "days_on_market": _to_int(dom),
            "listed_date": listed_date,
            "sold_date": sold_date,
            "photo_urls": json.dumps(photos),
            "source": "redfin",
            "redfin_url": home.get("url", ""),
        }

    except Exception as e:
        print(f"  ⚠ 解析listing失败: {e}")
        return None


def _nested(d: dict, *keys):
    """安全地从嵌套字典中取值"""
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k)
        else:
            return None
    return d


def _to_int(val):
    """安全转int"""
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


# ══════════════════════════════════════════════
# 写入数据库
# ══════════════════════════════════════════════

async def insert_listings(listings: list[dict]):
    """批量写入 listings 表, 重复的跳过"""
    conn = await asyncpg.connect(get_db_dsn())

    fields = [
        "listing_id", "address", "city", "state", "zipcode",
        "lat", "lng", "sqft", "bedrooms", "bathrooms",
        "lot_size", "year_built", "property_type", "hoa_fee", "parking",
        "list_price", "sold_price", "days_on_market",
        "listed_date", "sold_date", "photo_urls", "source", "redfin_url",
    ]

    cols = ", ".join(fields)
    vals = ", ".join(f"${i+1}" for i in range(len(fields)))

    # ON CONFLICT DO NOTHING = 如果listing_id已存在就跳过, 不更新
    sql = f"""
        INSERT INTO listings ({cols})
        VALUES ({vals})
        ON CONFLICT (listing_id) DO NOTHING
    """

    inserted = 0
    skipped = 0

    for listing in listings:
        # 构建参数元组
        record = []
        for f in fields:
            val = listing.get(f)
            # 日期字段需要转换
            if f in ("listed_date", "sold_date") and val:
                from datetime import date as date_type
                val = datetime.strptime(val, "%Y-%m-%d").date()
            # photo_urls 需要是 JSON 字符串
            if f == "photo_urls" and isinstance(val, list):
                val = json.dumps(val)
            record.append(val)

        try:
            result = await conn.execute(sql, *record)
            # result 格式: "INSERT 0 1" (成功) 或 "INSERT 0 0" (跳过)
            if "INSERT 0 1" in result:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  ⚠ 插入失败 [{listing.get('listing_id')}]: {e}")
            skipped += 1

    await conn.close()
    print(f"  已插入 {inserted} 条, 跳过 {skipped} 条 (重复或失败)")


# ══════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════

def _zips_for_city(city: str, state: str) -> list[str]:
    import zipcodes as _zc
    results = _zc.filter_by(city=city, state=state)
    if not results:
        raise SystemExit(f"No ZIP codes found for {city}, {state}")
    return [r["zip_code"] for r in results]


async def main():
    ap = argparse.ArgumentParser(description="Redfin sold listings scraper")
    zip_group = ap.add_mutually_exclusive_group(required=True)
    zip_group.add_argument(
        "--zip",
        nargs="+",
        metavar="ZIPCODE",
        help="One or more US ZIP codes, e.g. --zip 02135 02134",
    )
    zip_group.add_argument(
        "--city",
        metavar="CITY",
        help="City name — all ZIPs in the city are looked up automatically",
    )
    ap.add_argument(
        "--state",
        metavar="STATE",
        help="Two-letter state code, required with --city (e.g. MA, IL)",
    )
    ap.add_argument(
        "--market",
        default=None,
        help="Redfin market slug (e.g. 'boston', 'chicago'). Auto-derived if omitted.",
    )
    args = ap.parse_args()

    if args.city:
        if not args.state:
            ap.error("--state is required when using --city")
        target_zips = _zips_for_city(args.city, args.state)
        print(f"Resolved {args.city}, {args.state} → {len(target_zips)} ZIPs")
    else:
        target_zips = args.zip

    market: str = args.market or _market_for_zip(target_zips[0])

    print("=" * 50)
    print(f"Edensign BI — Redfin Scrape  ZIPs={target_zips}  market={market!r}")
    print("=" * 50)

    # 1. 从 Redfin 抓取数据
    print("\n[1/3] 抓取 Redfin 已售 listing...")
    raw_homes = await fetch_redfin_sold_listings(target_zips, market)

    if not raw_homes:
        print("  ❌ 没有获取到数据, 请检查网络或Redfin是否更改了API")
        return

    # 2. 解析每条 listing
    print("\n[2/3] 解析 listing 数据...")
    listings = []
    for home in raw_homes:
        parsed = parse_listing(home)
        if parsed:
            listings.append(parsed)

    print(f"  成功解析 {len(listings)} / {len(raw_homes)} 条listing")

    # 打印前5条预览
    print("\n  预览前5条:")
    for i, l in enumerate(listings[:5]):
        addr = l['address'][:40]
        price = l['sold_price'] or 0
        beds = l['bedrooms'] or '?'
        sqft = l['sqft'] or '?'
        dom = l['days_on_market'] or '?'
        photos = len(json.loads(l['photo_urls'])) if l['photo_urls'] else 0
        print(f"    {i+1}. {addr:42s} ${price:>10,}  {beds}BR  {sqft}sqft  {dom}days  {photos}photos")

    # 3. 写入数据库
    print(f"\n[3/3] 写入数据库...")
    await insert_listings(listings)

    # 统计摘要
    prices = [l['sold_price'] for l in listings if l['sold_price']]
    if prices:
        avg_price = sum(prices) // len(prices)
        min_price = min(prices)
        max_price = max(prices)
        print(f"\n  📊 价格统计:")
        print(f"     平均: ${avg_price:,}")
        print(f"     最低: ${min_price:,}")
        print(f"     最高: ${max_price:,}")

    types = {}
    for l in listings:
        t = l.get('property_type', 'Unknown')
        types[t] = types.get(t, 0) + 1
    print(f"\n  🏠 房屋类型分布:")
    for t, count in sorted(types.items(), key=lambda x: -x[1]):
        print(f"     {t}: {count}")

    print(f"\n✅ Done! {len(listings)} listings loaded for ZIPs {target_zips}.")


if __name__ == "__main__":
    asyncio.run(main())
