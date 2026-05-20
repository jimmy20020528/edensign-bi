"""
Edensign BI — Redfin 详情页爬虫
==================================================
用现有 listings.redfin_url 拼完整 URL,并发抓详情页 HTML,补:
- photo_urls(Redfin GIS 已经不返,只能从详情页拿)
- list_price 真值(从 priceHistory 的 Listed 事件)
- listed_date 真值
- 真 days_on_market(sold_date - listed_date)
- sold_price 真值(对照 GIS 已有值,通常一致)

用法:
    cd /Users/jimmy20020528/Desktop/Edensign/bi
    source .venv/bin/activate

    # 1) 先小批 smoke test (3 条)
    python scripts/redfin_detail_scrape.py --limit 3

    # 2) 跑全部没照片的 listing(默认行为)
    python scripts/redfin_detail_scrape.py

    # 3) 全部 listing 都重抓(用于刷新 priceHistory)
    python scripts/redfin_detail_scrape.py --all

字段保护原则:
  - photo_urls 当前为 [] 才覆盖,非空保留(已分类的别动)
  - list_price/sold_price/dates/dom 用 COALESCE,不覆盖已有非空值
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

MAX_CONCURRENCY = 5            # 并发上限,>10 容易触发 Cloudflare
SLEEP_BASE = 1.2               # 每请求前最小等待秒
SLEEP_JITTER = 1.0             # 抖动 0-1.0s
TIMEOUT = 30.0
BATCH_LOG_EVERY = 1            # 每完成 N 条打印一次

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


def build_headers(ua: str) -> dict[str, str]:
    """模拟浏览器导航请求,headers 太单薄会被 Cloudflare 直接 challenge。"""
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
# HTML 解析
# ════════════════════════════════════════════

def _extract_cache_response(html: str, key: str) -> dict | None:
    """
    Redfin 详情页是 SPA,数据走 /stingray/api/home/details/* 端点。
    服务端把这些 API 的响应 body 缓存在 __reactServerState.InitialContext
    .ReactServerAgent.cache.dataCache 里,以 url 路径为 key,res.text 为响应体。
    我们直接抠这块缓存,不再做表层 regex 抓字段。

    格式: <key>":{...,"res":{...,"text":"<JSON-escaped string>","_hasBody":true}}
    text 内容是 '{}&&{...真实JSON...}',要剥前缀再解析。
    """
    idx = html.find(f'{key}":{{')
    if idx < 0:
        return None
    text_marker = '"text":"'
    text_idx = html.find(text_marker, idx)
    if text_idx < 0:
        return None
    text_start = text_idx + len(text_marker)

    # 走到第一个未转义的 "
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
        # 借 json.loads 解 JS 字符串转义(/ \" \\ 等)
        decoded = json.loads('"' + raw + '"')
    except json.JSONDecodeError:
        return None

    if decoded.startswith("{}&&"):
        decoded = decoded[4:]

    try:
        return json.loads(decoded)
    except json.JSONDecodeError:
        return None


def _parse_photos_from_above(above: dict) -> list[str]:
    """从 aboveTheFold.payload.mediaBrowserInfo.photos[].photoUrls.fullScreenPhotoUrl"""
    payload = (above or {}).get("payload") or {}
    mbi = payload.get("mediaBrowserInfo") or {}
    photos = mbi.get("photos") or []
    out: list[str] = []
    seen: set[str] = set()
    for p in photos:
        if not isinstance(p, dict):
            continue
        urls = p.get("photoUrls") or {}
        url = urls.get("fullScreenPhotoUrl") or urls.get("nonFullScreenPhotoUrl")
        if isinstance(url, str) and url.startswith("http") and url not in seen:
            seen.add(url)
            out.append(url)
        if len(out) >= 15:
            break
    # 兜底:addressSectionInfo.primaryPhotoUrl
    if not out:
        primary = (payload.get("addressSectionInfo") or {}).get("primaryPhotoUrl")
        if isinstance(primary, str) and primary.startswith("http"):
            out.append(primary)
    return out


def _parse_events_from_below(below: dict) -> list[dict]:
    """从 belowTheFold.payload.propertyHistoryInfo.events[]"""
    payload = (below or {}).get("payload") or {}
    phi = payload.get("propertyHistoryInfo") or {}
    raw_events = phi.get("events") or []
    out: list[dict] = []
    for ev in raw_events:
        if not isinstance(ev, dict):
            continue
        desc = (ev.get("eventDescription") or "").strip()
        ts = ev.get("eventDate")
        price = ev.get("price")
        date_str: str | None = None
        if isinstance(ts, (int, float)):
            try:
                date_str = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
            except (OSError, ValueError):
                date_str = None
        out.append(
            {
                "description": desc,
                "date": date_str,
                "price": int(price) if isinstance(price, (int, float)) else None,
            }
        )
    return out


def parse_detail_html(html: str) -> dict:
    """
    从详情页 HTML(SPA shell + 预加载 API cache)抠出:
      photo_urls (list[str])
      list_price (int|None)        ← 'Listed' 事件价格
      sold_price (int|None)        ← 'Sold' 事件价格
      listed_date (str|None YYYY-MM-DD)
      sold_date (str|None)
      days_on_market (int|None)    ← 优先 sold-listed 差,备用 cumulativeDaysOnMarket
    """
    result: dict = {
        "photo_urls": [],
        "list_price": None,
        "sold_price": None,
        "listed_date": None,
        "sold_date": None,
        "days_on_market": None,
    }

    above = _extract_cache_response(html, "aboveTheFold")
    below = _extract_cache_response(html, "belowTheFold")

    if above:
        result["photo_urls"] = _parse_photos_from_above(above)

    events = _parse_events_from_below(below) if below else []
    listed_event = None
    sold_event = None
    for ev in events:
        d = ev["description"].lower()
        if "listed" in d and "delisted" not in d and listed_event is None:
            listed_event = ev
        if "sold" in d and sold_event is None:
            sold_event = ev

    if listed_event:
        result["list_price"] = listed_event["price"]
        result["listed_date"] = listed_event["date"]
    if sold_event:
        result["sold_price"] = sold_event["price"]
        result["sold_date"] = sold_event["date"]

    if result["listed_date"] and result["sold_date"]:
        try:
            d_l = datetime.strptime(result["listed_date"], "%Y-%m-%d")
            d_s = datetime.strptime(result["sold_date"], "%Y-%m-%d")
            result["days_on_market"] = max(0, (d_s - d_l).days)
        except ValueError:
            pass

    # 备用 DOM:从 aboveTheFold.addressSectionInfo.cumulativeDaysOnMarket
    if result["days_on_market"] is None and above:
        asi = (above.get("payload") or {}).get("addressSectionInfo") or {}
        cdom = asi.get("cumulativeDaysOnMarket")
        if isinstance(cdom, (int, float)):
            result["days_on_market"] = int(cdom)

    return result


# ════════════════════════════════════════════
# 抓取 + 写库
# ════════════════════════════════════════════

# 调试用:抓的第一份 HTML 落盘,方便修 parse pattern
_DEBUG_DUMP_PATH = Path("/tmp/redfin_detail_dump.html")
_DEBUG_DUMPED = False


async def fetch_one(
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
    listing_id: str,
    url_path: str,
) -> tuple[str, dict | None, str]:
    """单次抓取 + 解析,返回 (listing_id, parsed_dict_or_None, error_msg)"""
    global _DEBUG_DUMPED
    full_url = f"https://www.redfin.com{url_path}"
    ua = random.choice(USER_AGENTS)
    async with sem:
        # 抖动错峰,避免同时打 Redfin
        await asyncio.sleep(SLEEP_BASE + random.uniform(0, SLEEP_JITTER))
        try:
            resp = await client.get(full_url, headers=build_headers(ua))
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            return listing_id, None, f"HTTP {e.response.status_code}"
        except Exception as e:
            return listing_id, None, f"req error: {e}"

        # 第一份 HTML 落盘,后续修 parse pattern 用
        if not _DEBUG_DUMPED:
            try:
                _DEBUG_DUMP_PATH.write_text(resp.text, encoding="utf-8")
                print(
                    f"  [debug] 第一份 HTML 落盘到 {_DEBUG_DUMP_PATH} "
                    f"({len(resp.text)} 字)"
                )
                _DEBUG_DUMPED = True
            except Exception:
                pass

        try:
            parsed = parse_detail_html(resp.text)
        except Exception as e:
            return listing_id, None, f"parse error: {e}"

        return listing_id, parsed, ""


async def update_listing(
    conn: asyncpg.Connection, listing_id: str, parsed: dict
) -> None:
    """
    写库逻辑:
      - photo_urls 仅在原值为空数组时覆盖
      - 其它字段 COALESCE,不覆盖已有非空值
    """
    photo_json = (
        json.dumps(parsed["photo_urls"]) if parsed["photo_urls"] else None
    )
    listed_date = (
        datetime.strptime(parsed["listed_date"], "%Y-%m-%d").date()
        if parsed["listed_date"]
        else None
    )
    sold_date = (
        datetime.strptime(parsed["sold_date"], "%Y-%m-%d").date()
        if parsed["sold_date"]
        else None
    )

    # 字段保护逻辑:
    #   - photos: 库里 [] 才覆盖
    #   - sold_price/sold_date: COALESCE,不覆盖已有
    #   - list_price/listed_date/dom: 只有当 (库里 sold_date 是 NULL) AND
    #     (新数据本身不带 sold_date,即不是新一笔成交) 才填,避免把 2026 重新挂牌
    #     的数据跟 2024 的历史成交拼在一行里造成时序错位
    await conn.execute(
        """
        UPDATE listings SET
          photo_urls = CASE
            WHEN jsonb_array_length(photo_urls) = 0 AND $2::jsonb IS NOT NULL
              THEN $2::jsonb
            ELSE photo_urls
          END,
          sold_price = COALESCE(sold_price, $4::int),
          sold_date = COALESCE(sold_date, $6::date),
          list_price = CASE
            WHEN list_price IS NOT NULL THEN list_price
            WHEN sold_date IS NOT NULL AND $6::date IS NULL THEN list_price  -- 库存历史成交,新页是 active relisting,不污染
            ELSE $3::int
          END,
          listed_date = CASE
            WHEN listed_date IS NOT NULL THEN listed_date
            WHEN sold_date IS NOT NULL AND $6::date IS NULL THEN listed_date
            ELSE $5::date
          END,
          days_on_market = CASE
            WHEN days_on_market IS NOT NULL THEN days_on_market
            WHEN sold_date IS NOT NULL AND $6::date IS NULL THEN days_on_market
            ELSE $7::int
          END
        WHERE listing_id = $1
        """,
        listing_id,
        photo_json,
        parsed["list_price"],
        parsed["sold_price"],
        listed_date,
        sold_date,
        parsed["days_on_market"],
    )


async def main(
    only_missing_photos: bool,
    limit: int | None,
    past_year_only: bool = False,
) -> None:
    conn = await asyncpg.connect(get_db_dsn())

    where = ["source = 'redfin'", "redfin_url IS NOT NULL"]
    if only_missing_photos:
        where.append("jsonb_array_length(photo_urls) = 0")
    if past_year_only:
        # 只处理过去 1 年内 sold 或当前 active 的 listing
        # 旧 listing(2020-2024 sold)留库不动,Phase 2 再说
        where.append(
            "(sold_date >= CURRENT_DATE - INTERVAL '365 days' "
            "OR sold_date IS NULL)"
        )
    where_sql = " AND ".join(where)
    limit_sql = f"LIMIT {int(limit)}" if limit else ""

    rows = await conn.fetch(
        f"""
        SELECT listing_id, redfin_url
        FROM listings
        WHERE {where_sql}
        ORDER BY listing_id
        {limit_sql}
        """
    )
    if not rows:
        print("✓ 没有需要补的 listing。")
        await conn.close()
        return

    n = len(rows)
    print(
        f"开始抓取 {n} 条详情页 "
        f"(并发={MAX_CONCURRENCY}, sleep_base={SLEEP_BASE}s+随机{SLEEP_JITTER}s)"
    )
    print(f"过滤条件: {where_sql}\n")

    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    success = 0
    photos_added = 0
    list_price_added = 0
    listed_date_added = 0
    dom_added = 0
    errors: list[tuple[str, str]] = []

    # http2 需要 h2 包,本项目未装,用 HTTP/1.1 + Keep-Alive 已够
    async with httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=True
    ) as client:
        tasks = [
            fetch_one(sem, client, r["listing_id"], r["redfin_url"]) for r in rows
        ]

        for i, fut in enumerate(asyncio.as_completed(tasks), start=1):
            listing_id, parsed, err = await fut
            if err:
                errors.append((listing_id, err))
                print(f"  [{i:>3}/{n}] {listing_id} ❌ {err}")
                continue

            n_photos = len(parsed["photo_urls"])
            list_price = parsed["list_price"]
            listed_date = parsed["listed_date"]
            dom = parsed["days_on_market"]

            await update_listing(conn, listing_id, parsed)
            success += 1
            if n_photos > 0:
                photos_added += 1
            if list_price is not None:
                list_price_added += 1
            if listed_date is not None:
                listed_date_added += 1
            if dom is not None:
                dom_added += 1

            print(
                f"  [{i:>3}/{n}] {listing_id} ✓ "
                f"photos={n_photos:>2} list_price={list_price} "
                f"listed={listed_date} dom={dom}"
            )

    await conn.close()

    print(
        f"\n=== 完成 ===\n"
        f"  成功: {success}/{n}\n"
        f"  失败: {len(errors)}\n"
        f"  含照片解析: {photos_added}\n"
        f"  含 list_price: {list_price_added}\n"
        f"  含 listed_date: {listed_date_added}\n"
        f"  含 days_on_market: {dom_added}"
    )
    if errors:
        print(f"\n失败前 10:")
        for lid, err in errors[:10]:
            print(f"  {lid}: {err}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument(
        "--all",
        action="store_true",
        help="抓所有 redfin listing(默认只抓没照片的)",
    )
    p.add_argument(
        "--limit", type=int, default=None, help="限条数,smoke test 用"
    )
    p.add_argument(
        "--past-year-only",
        action="store_true",
        help="只处理过去 1 年 sold 或当前 active 的 listing(George 限定的训练范围)",
    )
    args = p.parse_args()
    asyncio.run(
        main(
            only_missing_photos=not args.all,
            limit=args.limit,
            past_year_only=args.past_year_only,
        )
    )
