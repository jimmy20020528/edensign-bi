"""
Edensign BI — Redfin 发现脚本
==================================================
扫 redfin.com/zipcode/<zip>/filter/... 搜索页 HTML,从 InitialContext.dataCache
抠 homes 列表,把新 listing_id + redfin_url 占位进 listings 表。

为什么不走 GIS JSON API:
  - GIS 端点单查询硬上限 350,且 polygon/page_number/sold_within_days 当前都被忽略
  - 同样 350 集即使变排序也是同一批

为什么搜索页 HTML 行得通:
  - 搜索页是 Redfin 自己的 SPA shell,InitialContext 里预加载了一份 homes
  - 不同 (zip, time_filter) 组合的 URL 命中不同 server-side cache,homes 集合不同
  - 求并集就突破 350 cap

后续:
  跑完后接力 redfin_detail_scrape.py 把每条详情页的 photos + priceHistory 补全。

用法:
    cd /Users/jimmy20020528/Desktop/Edensign/bi
    source .venv/bin/activate

    # smoke test 单 ZIP 单 filter
    python scripts/redfin_discover.py --zip 02134 --filter sold-1yr

    # 全部目标(默认 02134+02135 × 多个 time filter)
    python scripts/redfin_discover.py --all
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

# 直接打 Redfin 内部 GIS API,用 backend 工程师确认有效的参数:
#   region_id=640 (Allston+Brighton 复合区,一次查 2 ZIP)
#   mpt=99 (max property type tier,放开限制)
#   sold_within_days=1825 (5 年内 sold)
#   start 翻页(每 350 条一页,真分页有效)
DEFAULT_REGION_IDS = [
    639,   # 02134 (Allston) — 真总数 1000+ 条 sold-5yr,需翻页
    640,   # 02135 (Brighton) — 真总数 1500+ 条 sold-5yr,需翻页
]
DEFAULT_MPT = 99
NUM_HOMES_PER_PAGE = 350
MAX_PAGES = 20  # 5 年 Allston/Brighton 1800+ 条,~6 页就拿全;留 20 页安全余量

# 不同 status × sold_within_days 组合,各自分页
QUERY_COMBOS = [
    {"name": "sold-5yr", "status": 9, "sold_within_days": 1825},
    {"name": "sold-3yr", "status": 9, "sold_within_days": 1095},  # 备选,不同时间锚可能返不同 350
    {"name": "active",   "status": 1, "sold_within_days": None},
]

MAX_CONCURRENCY = 3
SLEEP_BASE = 1.5
SLEEP_JITTER = 1.5
TIMEOUT = 45.0

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]


def build_headers(ua: str) -> dict[str, str]:
    return {
        "User-Agent": ua,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "max-age=0",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }


# ════════════════════════════════════════════
# InitialContext 缓存抠取(沿用 detail_scrape 的写法)
# ════════════════════════════════════════════

def _strip_redfin_prefix(text: str) -> str:
    """Redfin 内部端点返回带 '{}&&' 前缀,去掉之后才是合法 JSON。"""
    idx = text.find('{"')
    if idx == -1:
        idx = text.find('{')
    return text[idx:] if idx >= 0 else text


def _extract_cache_response(html: str, key: str) -> dict | None:
    """从 __reactServerState.InitialContext.ReactServerAgent.cache.dataCache 抠 res.text"""
    idx = html.find(f'{key}":{{')
    if idx < 0:
        return None
    text_marker = '"text":"'
    text_idx = html.find(text_marker, idx)
    if text_idx < 0:
        return None
    text_start = text_idx + len(text_marker)

    i = text_start
    escape = False
    while i < len(html):
        c = html[i]
        if escape:
            escape = False
        elif c == "\\":
            escape = True
        elif c == '"':
            break
        i += 1
    raw = html[text_start:i]

    try:
        decoded = json.loads('"' + raw + '"')
    except json.JSONDecodeError:
        return None

    if decoded.startswith("{}&&"):
        decoded = decoded[4:]

    try:
        return json.loads(decoded)
    except json.JSONDecodeError:
        return None


def _find_all_cache_keys_with_homes(html: str) -> list[tuple[str, list[dict]]]:
    """
    扫所有 dataCache 入口,返回 [(key, homes_list)] 凡是 payload 里有 homes 数组的。
    搜索页 cache key 是完整 URL(如 \\u002Fstingray\\u002Fapi\\u002Fgis?al=1&...),
    可能很长(数百字),只匹 stingray 路径开头 + 走到 cache 入口的 ":{ 直到下一个 ".

    优先抓 gis* 端点(它的 payload 才装 homes),跳 region/market/aggregates 等。
    """
    results: list[tuple[str, list[dict]]] = []

    # 完整 cache key 是 "<some-very-long-stingray-url>":{"url":"\\u002Fstingray..."
    # 用 stingray 路径开头作锚,允许 key 长达 1500 字
    for m in re.finditer(
        r'"(\\u002Fstingray\\u002F[^"]{1,1500}?)"\s*:\s*\{\s*"url":\s*"\\u002Fstingray',
        html,
    ):
        key = m.group(1)
        # 只看 gis* 端点(其它如 region/market/avm 不含 homes)
        # gis / gis-csv / gis-aggregates 都试
        if "gis" not in key.lower():
            continue
        cache = _extract_cache_response(html, key)
        if not cache:
            continue
        payload = cache.get("payload") or {}
        homes = payload.get("homes")
        if isinstance(homes, list) and homes:
            # 截取 key 末尾片段做日志(完整太长)
            short_key = key.split("?", 1)[0].replace("\\u002F", "/")
            results.append((short_key, homes))
    return results


# ════════════════════════════════════════════
# 解析单条 home(简化,详情后续 detail_scrape 补)
# ════════════════════════════════════════════

def _nested(d, *keys):
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k)
        else:
            return None
    return d


def _to_int(val):
    if val is None:
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


PROPERTY_TYPE_MAP = {
    1: "Single Family",
    2: "Condo",
    3: "Townhouse",
    4: "Multi-Family",
    5: "Land",
    6: "Other",
    13: "Condo",
}


def parse_home(home: dict) -> dict | None:
    mls_id = _nested(home, "mlsId", "value")
    prop_id = home.get("propertyId")
    listing_id = str(mls_id or prop_id or "")
    if not listing_id:
        return None

    lat_long = _nested(home, "latLong", "value") or {}
    sold_ts = home.get("soldDate")
    sold_date = (
        datetime.fromtimestamp(sold_ts / 1000).strftime("%Y-%m-%d")
        if isinstance(sold_ts, (int, float))
        else None
    )
    listed_ts = home.get("listingAddedDate")
    listed_date = (
        datetime.fromtimestamp(listed_ts / 1000).strftime("%Y-%m-%d")
        if isinstance(listed_ts, (int, float))
        else None
    )

    dom_field = home.get("dom") or home.get("timeOnRedfin")
    dom = dom_field.get("value") if isinstance(dom_field, dict) else dom_field

    sold_price = _nested(home, "price", "value")
    list_price = _nested(home, "listPrice") or _nested(home, "price", "value")

    street = _nested(home, "streetLine", "value") or ""
    city = home.get("city") or "Allston"
    state = home.get("state") or "MA"
    zipcode = home.get("zip") or ""

    return {
        "listing_id": f"rf_{listing_id}",
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
        "property_type": PROPERTY_TYPE_MAP.get(home.get("propertyType"), "Other"),
        "hoa_fee": _to_int(_nested(home, "hoa", "value")),
        "list_price": _to_int(list_price),
        "sold_price": _to_int(sold_price),
        "days_on_market": _to_int(dom),
        "listed_date": listed_date,
        "sold_date": sold_date,
        "redfin_url": home.get("url", ""),
    }


# ════════════════════════════════════════════
# 抓取 + 写库
# ════════════════════════════════════════════

_DEBUG_DUMPED: set[str] = set()


async def fetch_via_gis_paginated(
    client: httpx.AsyncClient,
    region_id: int,
    combo: dict,
) -> tuple[str, list[dict]]:
    """
    直接打 /stingray/api/gis,用 start 参数真分页,直到拿完或拿到重复。
    每页 350 条,5 年 Allston/Brighton ~1800 条,大约 6 页拿完。
    """
    label = f"region={region_id}/{combo['name']}"
    base_url = "https://www.redfin.com/stingray/api/gis"
    homes: list[dict] = []
    seen: set[str] = set()

    for page in range(MAX_PAGES):
        start = page * NUM_HOMES_PER_PAGE
        params = {
            "al": 1,
            "include_nearby_homes": "true",
            "market": "boston",
            "mpt": DEFAULT_MPT,
            "num_homes": NUM_HOMES_PER_PAGE,
            "ord": "redfin-recommended-asc",
            "page_number": 1,
            "region_id": region_id,
            "region_type": 2,
            "sf": "1,2,3,5,6,7",
            "start": start,
            "status": combo["status"],
            "uipt": "1,2,3,4,5,6,7,8",
            "v": 8,
        }
        if combo.get("sold_within_days"):
            params["sold_within_days"] = combo["sold_within_days"]

        ua = random.choice(USER_AGENTS)
        try:
            resp = await client.get(base_url, params=params, headers=build_headers(ua))
            resp.raise_for_status()
        except Exception as e:
            print(f"    ⚠ {label} page {page+1} (start={start}) 请求失败: {e}")
            break

        try:
            data = json.loads(_strip_redfin_prefix(resp.text))
        except json.JSONDecodeError:
            print(f"    ⚠ {label} page {page+1} JSON 解析失败")
            break

        page_homes = data.get("payload", {}).get("homes") or []
        new_count = 0
        for h in page_homes:
            pid = str(h.get("propertyId") or "")
            if pid and pid not in seen:
                seen.add(pid)
                homes.append(h)
                new_count += 1

        print(
            f"    page {page+1:>2} (start={start:>4}): 收到 {len(page_homes):>3}, "
            f"新增 {new_count:>3} (累计 {len(homes)})"
        )

        # 这一页没满 350 = 数据已尽
        if len(page_homes) < NUM_HOMES_PER_PAGE:
            break
        # 这一页全是已见过的 = 锚定循环结束
        if new_count == 0:
            print(f"    (新增 0,已饱和)")
            break

        await asyncio.sleep(SLEEP_BASE + random.uniform(0, SLEEP_JITTER))

    return label, homes


async def insert_seeds(
    conn: asyncpg.Connection, parsed_list: list[dict]
) -> tuple[int, int]:
    """ON CONFLICT DO NOTHING 写库,只填已知字段。详情靠后续 detail_scrape 补。"""
    fields = [
        "listing_id", "address", "city", "state", "zipcode",
        "lat", "lng", "sqft", "bedrooms", "bathrooms",
        "lot_size", "year_built", "property_type", "hoa_fee",
        "list_price", "sold_price", "days_on_market",
        "listed_date", "sold_date", "redfin_url",
    ]
    cols = ", ".join(fields)
    vals = ", ".join(f"${i+1}" for i in range(len(fields)))
    sql = (
        f"INSERT INTO listings ({cols}, photo_urls, source) "
        f"VALUES ({vals}, '[]'::jsonb, 'redfin') "
        f"ON CONFLICT (listing_id) DO NOTHING"
    )

    inserted, skipped = 0, 0
    for p in parsed_list:
        record = []
        for f in fields:
            v = p.get(f)
            if f in ("listed_date", "sold_date") and v:
                v = datetime.strptime(v, "%Y-%m-%d").date()
            record.append(v)
        try:
            res = await conn.execute(sql, *record)
            if "INSERT 0 1" in res:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"    ⚠ 插入失败 [{p.get('listing_id')}]: {e}")
            skipped += 1
    return inserted, skipped


async def main(zips: list[str], region_ids: list[int]) -> None:
    print(f"目标 ZIPs (后过滤用): {zips}")
    print(f"Region IDs (Redfin 内部): {region_ids}")
    total_combos = len(region_ids) * len(QUERY_COMBOS)
    print(f"共 {total_combos} 个 region×combo,每个最多 {MAX_PAGES} 页翻页\n")

    raw_homes: list[dict] = []
    seen_pids: set[str] = set()

    async with httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=True
    ) as client:
        for region_id in region_ids:
            for combo in QUERY_COMBOS:
                print(f"\n[region={region_id} / {combo['name']}]")
                _, homes = await fetch_via_gis_paginated(client, region_id, combo)
                new = 0
                for h in homes:
                    pid = str(h.get("propertyId") or "")
                    if pid and pid not in seen_pids:
                        seen_pids.add(pid)
                        raw_homes.append(h)
                        new += 1
                print(f"  小计:此 combo 拉到 {len(homes)} 条,跨 combo 新增 {new}")

    print(f"\n  跨 combo 去重后共 {len(raw_homes)} 条 raw")

    # 后过滤到目标 ZIP(region_id=640 是复合区,会带进相邻 ZIP)
    target = set(zips)
    in_target = [h for h in raw_homes if h.get("zip") in target]
    print(f"  过滤到 {sorted(target)} 后 {len(in_target)} 条")

    if not in_target:
        print("  ⚠ 没拿到任何 listings — 检查 region_id 是否对")
        return

    parsed = [p for p in (parse_home(h) for h in in_target) if p]
    print(f"  解析成功 {len(parsed)} / {len(in_target)}")

    conn = await asyncpg.connect(get_db_dsn())
    inserted, skipped = await insert_seeds(conn, parsed)
    await conn.close()

    print(f"\n=== 完成 ===")
    print(f"  库里新插入: {inserted}")
    print(f"  跳过(已存在): {skipped}")
    print(
        f"\n下一步:跑 detail_scrape 给新 listing 补照片和 priceHistory:\n"
        f"    python scripts/redfin_detail_scrape.py"
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--zip", action="append", help="目标 ZIP(后过滤用,可多次)")
    p.add_argument(
        "--region-id",
        type=int,
        action="append",
        help="Redfin 内部 region_id(如 640 = Allston+Brighton 复合区)",
    )
    p.add_argument("--all", action="store_true", help="跑默认全集")
    args = p.parse_args()

    if args.all or (not args.zip and not args.region_id):
        zips = DEFAULT_ZIPS
        region_ids = DEFAULT_REGION_IDS
    else:
        zips = args.zip or DEFAULT_ZIPS
        region_ids = args.region_id or DEFAULT_REGION_IDS

    asyncio.run(main(zips, region_ids))
