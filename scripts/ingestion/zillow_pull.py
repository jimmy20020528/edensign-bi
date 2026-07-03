"""
Edensign BI — Zillow Sold Listings Pull
==================================================
抓 zillow.com 公开 search 页 HTML,从内嵌 "listResults" JSON 抠 listings,
写入 listings 表 source='zillow',listing_id='zw_<zpid>'。

不需要 API key,Zillow HTML 公开可见。Akamai 偶尔 challenge,需 random UA + 间隔。

字段映射详见 FACTORS.md;跟 Redfin 数据共享 schema,跨源 dedup 用 canonical_id。

用法:
    cd /Users/jimmy20020528/Desktop/Edensign/bi
    source .venv/bin/activate

    # smoke test 单 ZIP 单 page
    python scripts/zillow_pull.py --zip 02135 --max-pages 1

    # 全集(02134 + 02135 × 12m sold)
    python scripts/zillow_pull.py --all

    # 只抓 02135 过去 6 个月
    python scripts/zillow_pull.py --zip 02135 --doz 6m
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import asyncpg
import httpx

_scripts = Path(__file__).resolve().parent.parent  # scripts/ root
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from db_dsn import get_db_dsn  # noqa: E402

# ════════════════════════════════════════════
# 配置
# ════════════════════════════════════════════

DEFAULT_ZIPS = ["02134", "02135"]
ZIP_TO_CITY_SLUG = {
    "02134": "boston-ma",   # Allston is in Boston
    "02135": "boston-ma",   # Brighton is in Boston
}
DEFAULT_DOZ = "12m"  # 过去 12 个月 sold

NUM_PER_PAGE = 40   # Zillow 实测每页 ~40 条
MAX_PAGES = 30      # safety cap (Zillow 单 ZIP sold-12m 实测 < 500 条)

SLEEP_BASE = 2.0
SLEEP_JITTER = 1.5
TIMEOUT = 30.0

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

# Zillow homeType → 我们 schema 的 property_type
PROPERTY_TYPE_MAP = {
    "CONDO": "Condo",
    "SINGLE_FAMILY": "Single Family",
    "TOWNHOUSE": "Townhouse",
    "MULTI_FAMILY": "Multi-Family",
    "APARTMENT": "Apartment",
    "LOT": "Land",
    "MANUFACTURED": "Other",
    "COOPERATIVE": "Condo",
}


def build_headers(ua: str) -> dict[str, str]:
    """模拟浏览器 — Zillow 用 Akamai,headers 太单薄会被 challenge"""
    return {
        "user-agent": ua,
        "accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "max-age=0",
        "sec-ch-ua": '"Chromium";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
    }


def build_search_url(zipcode: str, page: int = 1, doz: str = "12m") -> str:
    """
    构造 Zillow sold search URL。

    重要:实测带 searchQueryState 的复杂 URL 会被 Akamai 拦(返 Access denied)。
    简单路径反而 work,Zillow 默认就是 12m sold。doz 参数当前忽略,留给未来扩展。

    翻页用路径形式: /sold/2_p/, /sold/3_p/ ...
    """
    city_slug = ZIP_TO_CITY_SLUG.get(zipcode, "boston-ma")
    page_path = f"{page}_p/" if page > 1 else ""
    return f"https://www.zillow.com/{city_slug}-{zipcode}/sold/{page_path}"


# ════════════════════════════════════════════
# 解析 Zillow HTML
# ════════════════════════════════════════════

def parse_list_results(html: str) -> list[dict]:
    """
    从 Zillow search 页 HTML 抠 'listResults':[...] 完整 JSON 数组。
    手动平衡括号(数组里有嵌套 object,不能简单 regex)。
    """
    marker = '"listResults":['
    start = html.find(marker)
    if start < 0:
        return []

    arr_start = start + len(marker) - 1  # 指向开头 [
    depth = 0
    in_string = False
    escape = False
    end = -1
    for i in range(arr_start, len(html)):
        c = html[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end < 0:
        return []

    try:
        return json.loads(html[arr_start:end])
    except json.JSONDecodeError:
        return []


def _to_int(val):
    if val is None:
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def parse_listing(raw: dict) -> dict | None:
    """把 Zillow listResult 单条 → 我们 schema 字段"""
    info = raw.get("hdpData", {}).get("homeInfo", {}) or {}
    zpid = info.get("zpid") or raw.get("zpid")
    if not zpid:
        return None

    home_status = (info.get("homeStatus") or "").upper()
    price = info.get("price")

    # 根据 status 区分 list_price 和 sold_price
    if home_status in ("RECENTLY_SOLD", "SOLD"):
        sold_price = _to_int(price)
        # Zillow 列表 view 不返历史 list_price,只能用 sold_price 当 fallback
        # detail page 才有 priceHistory(类似 Redfin)
        list_price = _to_int(info.get("lastSoldPrice")) or sold_price
    else:
        sold_price = _to_int(info.get("lastSoldPrice"))
        list_price = _to_int(price)

    sold_ts = info.get("dateSold")
    sold_date = None
    if isinstance(sold_ts, (int, float)) and sold_ts > 0:
        try:
            sold_date = datetime.fromtimestamp(sold_ts / 1000).strftime("%Y-%m-%d")
        except (OSError, ValueError):
            pass

    listed_ts = info.get("listingDateTimeStamp")
    listed_date = None
    if isinstance(listed_ts, (int, float)) and listed_ts > 0:
        try:
            listed_date = datetime.fromtimestamp(listed_ts / 1000).strftime("%Y-%m-%d")
        except (OSError, ValueError):
            pass

    street = info.get("streetAddress") or ""
    city = info.get("city") or "Boston"
    state = info.get("state") or "MA"
    zipcode = info.get("zipcode") or ""

    # photos: 列表 view 只有 imgSrc 一张,详情页才多
    img_src = raw.get("imgSrc") or info.get("hiResImageLink")
    photo_urls = [img_src] if isinstance(img_src, str) and img_src.startswith("http") else []

    # Zillow latLong 在 info 里
    lat = info.get("latitude")
    lng = info.get("longitude")

    return {
        "listing_id": f"zw_{zpid}",
        "address": f"{street}, {city}, {state} {zipcode}".strip(", "),
        "city": city,
        "state": state,
        "zipcode": zipcode,
        "lat": lat,
        "lng": lng,
        "sqft": _to_int(info.get("livingArea")),
        "bedrooms": _to_int(info.get("bedrooms")),
        "bathrooms": info.get("bathrooms"),
        "lot_size": _to_int(info.get("lotAreaValue")),
        "year_built": _to_int(info.get("yearBuilt")),
        "property_type": PROPERTY_TYPE_MAP.get(info.get("homeType"), "Other"),
        "hoa_fee": _to_int(info.get("hoaFee")),
        "list_price": list_price,
        "sold_price": sold_price,
        "days_on_market": _to_int(info.get("daysOnZillow")),
        "listed_date": listed_date,
        "sold_date": sold_date,
        "photo_urls": photo_urls,
        "zillow_url": raw.get("detailUrl"),
    }


# ════════════════════════════════════════════
# 抓取 + 翻页
# ════════════════════════════════════════════

async def fetch_zip_paginated(
    client: httpx.AsyncClient,
    zipcode: str,
    doz: str,
    max_pages: int,
) -> list[dict]:
    """对单 ZIP 翻页,每页 ~40 条,直到没有新 zpid"""
    homes: list[dict] = []
    seen_zpids: set[int] = set()

    for page in range(1, max_pages + 1):
        url = build_search_url(zipcode, page, doz)
        ua = random.choice(USER_AGENTS)

        try:
            resp = await client.get(url, headers=build_headers(ua), timeout=TIMEOUT)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            print(f"    ⚠ ZIP {zipcode} page {page} HTTP {e.response.status_code}")
            break
        except Exception as e:
            print(f"    ⚠ ZIP {zipcode} page {page} 请求失败: {e}")
            break

        html = resp.text

        # captcha 检测
        if "Press & Hold" in html or "px-captcha" in html:
            print(f"    ⚠ ZIP {zipcode} page {page} 撞 Akamai captcha,停")
            break

        page_listings = parse_list_results(html)
        if not page_listings:
            # 落盘帮调试
            dump = Path(f"/tmp/zillow_zip_{zipcode}_p{page}.html")
            try:
                dump.write_text(html, encoding="utf-8")
                print(f"    ⚠ ZIP {zipcode} page {page} 没找到 listResults,落盘 {dump}")
            except Exception:
                pass
            break

        new_count = 0
        for raw in page_listings:
            info = raw.get("hdpData", {}).get("homeInfo", {}) or {}
            zpid = info.get("zpid") or raw.get("zpid")
            if zpid and zpid not in seen_zpids:
                seen_zpids.add(zpid)
                homes.append(raw)
                new_count += 1

        print(
            f"    page {page:>2}: 收到 {len(page_listings):>3} 条, "
            f"新增 {new_count:>3} (累计 {len(homes)})"
        )

        # 不满 1 页 = 没下一页
        if len(page_listings) < NUM_PER_PAGE - 5:  # 容忍 ±5
            break
        # 没新增 = 翻不动了
        if new_count == 0:
            break

        await asyncio.sleep(SLEEP_BASE + random.uniform(0, SLEEP_JITTER))

    return homes


# ════════════════════════════════════════════
# 写库
# ════════════════════════════════════════════

async def insert_listings(
    conn: asyncpg.Connection, parsed_list: list[dict]
) -> tuple[int, int]:
    fields = [
        "listing_id", "address", "city", "state", "zipcode",
        "lat", "lng", "sqft", "bedrooms", "bathrooms",
        "lot_size", "year_built", "property_type", "hoa_fee",
        "list_price", "sold_price", "days_on_market",
        "listed_date", "sold_date", "photo_urls", "zillow_url",
    ]
    cols = ", ".join(fields)
    vals = ", ".join(f"${i+1}" for i in range(len(fields)))
    sql = (
        f"INSERT INTO listings ({cols}, source) "
        f"VALUES ({vals}, 'zillow') "
        f"ON CONFLICT (listing_id) DO NOTHING"
    )

    inserted, skipped = 0, 0
    for p in parsed_list:
        record = []
        for f in fields:
            v = p.get(f)
            if f in ("listed_date", "sold_date") and v:
                v = datetime.strptime(v, "%Y-%m-%d").date()
            if f == "photo_urls":
                v = json.dumps(v) if v else json.dumps([])
            record.append(v)
        try:
            res = await conn.execute(sql, *record)
            if "INSERT 0 1" in res:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"    ⚠ insert 失败 [{p.get('listing_id')}]: {e}")
            skipped += 1
    return inserted, skipped


async def main(zips: list[str], doz: str, max_pages: int) -> None:
    print(f"目标 ZIPs: {zips}")
    print(f"时间窗 doz: {doz} (Zillow 接受 6m/12m/24m/36m)")
    print(f"翻页上限: {max_pages}\n")

    raw_homes: list[dict] = []
    seen_zpids: set[int] = set()

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for zipcode in zips:
            print(f"[ZIP {zipcode}]")
            homes = await fetch_zip_paginated(client, zipcode, doz, max_pages)
            new = 0
            for h in homes:
                info = h.get("hdpData", {}).get("homeInfo", {}) or {}
                zpid = info.get("zpid") or h.get("zpid")
                if zpid and zpid not in seen_zpids:
                    seen_zpids.add(zpid)
                    raw_homes.append(h)
                    new += 1
            print(f"  小计:{len(homes)} 条,跨 ZIP 去重新增 {new}\n")

    print(f"=== 全部抓取完成,共 {len(raw_homes)} 条 unique ===\n")

    if not raw_homes:
        print("⚠ 没拿到任何 listings,看 /tmp/zillow_zip_*.html 找原因")
        return

    parsed = [p for p in (parse_listing(h) for h in raw_homes) if p]
    print(f"解析成功 {len(parsed)} / {len(raw_homes)}")

    # 后过滤到目标 ZIP(zillow region 可能带进相邻 ZIP)
    target = set(zips)
    in_target = [p for p in parsed if p["zipcode"] in target]
    print(f"过滤到 {sorted(target)} 后 {len(in_target)} 条")

    conn = await asyncpg.connect(get_db_dsn())
    inserted, skipped = await insert_listings(conn, in_target)
    await conn.close()

    print(f"\n=== 完成 ===")
    print(f"  库里新插入: {inserted}")
    print(f"  跳过(已存在): {skipped}")
    print(
        f"\n下一步:\n"
        f"  1. 跑 dedup_canonical.py 跨源去重(找 Zillow vs Redfin 同房)\n"
        f"  2. 可选:写 zillow_detail_scrape.py 补 photos / lat / lot_size 等"
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--zip", action="append", help="目标 ZIP(可多次)")
    p.add_argument("--doz", default=DEFAULT_DOZ, help="时间窗 6m/12m/24m/36m")
    p.add_argument(
        "--max-pages", type=int, default=MAX_PAGES, help="单 ZIP 翻页上限"
    )
    p.add_argument("--all", action="store_true", help="跑默认 ZIP 全集")
    args = p.parse_args()

    if args.all or not args.zip:
        zips = DEFAULT_ZIPS
    else:
        zips = args.zip

    asyncio.run(main(zips, args.doz, args.max_pages))
