"""
Edensign BI — Step 6: 每周市场快照

对给定 area(默认 'Allston'),在今天写入一行 market_snapshot:
  1. mortgage_rate_30yr — FRED API (MORTGAGE30US series)
  2. avg_close_days    — AVG(days_on_market) 来自 listings 过去 90 天成交
  3. price_reduction_pct — AVG((list - sold) / list * 100)

MVP 阶段只填这 3 个;扩充 active_inventory / absorption 等指标需先抓 active listing。

表 market_snapshots 有 UNIQUE(area_name, snapshot_date),同日重跑会 UPSERT 覆盖。
"""

import asyncio
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import asyncpg
import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ══════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════

DB_DSN = "postgresql://edensign:edensign_dev@localhost:5432/edensign_bi"
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES = "MORTGAGE30US"  # Freddie Mac 30 年固定房贷利率,周度数据
AREA_NAME = "Allston"
LOOKBACK_DAYS = 90  # 从过去多少天的 sold listings 聚合市场指标

FRED_API_KEY = os.environ.get("FRED_API_KEY")
FRED_ENABLED = FRED_API_KEY and not FRED_API_KEY.startswith("your_")


# ══════════════════════════════════════════════
# FRED API — 最新 30 年固定房贷利率
# ══════════════════════════════════════════════

async def fetch_mortgage_rate(client: httpx.AsyncClient) -> Optional[float]:
    """FRED MORTGAGE30US 最新一条观察值(Freddie Mac Primary Mortgage Market Survey)。"""
    if not FRED_ENABLED:
        print("  ✗ FRED_API_KEY 未设置,跳过利率拉取")
        return None
    params = {
        "series_id": FRED_SERIES,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",  # 最新的排第一
        "limit": 1,
    }
    try:
        r = await client.get(FRED_URL, params=params, timeout=15.0)
        r.raise_for_status()
        obs = r.json().get("observations", [])
        if obs and obs[0].get("value") not in (".", None):
            return float(obs[0]["value"])
    except Exception as e:
        print(f"  ✗ FRED 调用失败: {e}")
    return None


# ══════════════════════════════════════════════
# 从 listings 衍生市场指标
# ══════════════════════════════════════════════

async def compute_listing_metrics(conn: asyncpg.Connection) -> dict:
    """从 sold listings 聚合过去 90 天的市场指标。"""
    row = await conn.fetchrow(f"""
        SELECT
          COUNT(*) FILTER (
            WHERE sold_date >= CURRENT_DATE - INTERVAL '{LOOKBACK_DAYS} days'
          ) AS n_recent,
          AVG(days_on_market) FILTER (
            WHERE days_on_market IS NOT NULL
              AND days_on_market > 0
          ) AS avg_dom,
          AVG(
            (list_price - sold_price)::numeric / NULLIF(list_price, 0) * 100
          ) FILTER (
            WHERE list_price IS NOT NULL AND sold_price IS NOT NULL
              AND list_price > 0 AND sold_price > 0
          ) AS avg_reduction_pct
        FROM listings
    """)
    return {
        "n_recent": row["n_recent"] or 0,
        "avg_close_days": float(row["avg_dom"]) if row["avg_dom"] is not None else None,
        "price_reduction_pct": float(row["avg_reduction_pct"]) if row["avg_reduction_pct"] is not None else None,
    }


# ══════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════

async def main():
    print("=" * 50)
    print("Edensign BI — Step 6: Market snapshot")
    print("=" * 50)
    print(f"  Area: {AREA_NAME}")
    print(f"  Date: {date.today()}")
    print(f"  FRED: {'✓ 启用' if FRED_ENABLED else '✗ 未设置 key,跳过利率'}")

    conn = await asyncpg.connect(DB_DSN)
    http = httpx.AsyncClient()

    # 并发拉利率 + 聚合 listing 指标
    rate_task = fetch_mortgage_rate(http)
    metrics_task = compute_listing_metrics(conn)
    rate, metrics = await asyncio.gather(rate_task, metrics_task)
    await http.aclose()

    print(f"\n📊 拉取结果:")
    print(f"  mortgage_rate_30yr  = {rate if rate else 'N/A'}")
    print(f"  avg_close_days      = {metrics['avg_close_days']:.1f}"
          if metrics['avg_close_days'] is not None else "  avg_close_days      = N/A")
    print(f"  price_reduction_pct = {metrics['price_reduction_pct']:.2f}%"
          if metrics['price_reduction_pct'] is not None else "  price_reduction_pct = N/A")
    print(f"  n_listings (参考)    = {metrics['n_recent']} (过去 {LOOKBACK_DAYS} 天)")

    # UPSERT 写入 market_snapshots
    await conn.execute("""
        INSERT INTO market_snapshots (
            area_name, snapshot_date,
            mortgage_rate_30yr, avg_close_days, price_reduction_pct
        ) VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (area_name, snapshot_date)
        DO UPDATE SET
          mortgage_rate_30yr = EXCLUDED.mortgage_rate_30yr,
          avg_close_days = EXCLUDED.avg_close_days,
          price_reduction_pct = EXCLUDED.price_reduction_pct,
          created_at = NOW()
    """, AREA_NAME, date.today(), rate,
         metrics["avg_close_days"], metrics["price_reduction_pct"])

    await conn.close()

    print(f"\n✅ 写入 market_snapshots: area={AREA_NAME}, date={date.today()}")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
