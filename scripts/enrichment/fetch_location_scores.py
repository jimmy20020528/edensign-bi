"""
Edensign BI — Step 5: 给每条 listing 采集 Location 因子

数据源:
  1. PostGIS 最近中心点匹配 → tract_id(把 listing 连接到 census_tracts)
  2. Overpass API (OpenStreetMap) → amenity_count_1km, nearest_transit_m, nearest_park_m
  3. Walk Score API → walk_score, transit_score, bike_score(若设置了 WALKSCORE_API_KEY)
  4. FEMA NFHL → flood_zone

所有 API 调用对未设置 key 的跳过,方便增量补全。
"""

import argparse
import asyncio
import json
import math
import os
import sys
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
# Overpass 有多个公开 mirror,循环使用减轻单个 endpoint 压力
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
WALKSCORE_URL = "https://api.walkscore.com/score"
FEMA_NFHL_URL = "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28/query"
CENSUS_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
REQUEST_DELAY_SEC = 3.0  # Overpass 对 free endpoint 限得严,保守到 3 秒
OVERPASS_RETRY_MAX = 3   # 429/504 时最多重试几次

WALKSCORE_KEY = os.environ.get("WALKSCORE_API_KEY")
WALKSCORE_ENABLED = WALKSCORE_KEY and not WALKSCORE_KEY.startswith("your_")


# ══════════════════════════════════════════════
# 距离计算(Haversine 公式)
# ══════════════════════════════════════════════

def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """两个 lat/lng 点之间的地球表面距离(米)。误差 <0.5%,MVP 足够。"""
    R = 6371000  # 地球平均半径,米
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ══════════════════════════════════════════════
# Overpass — OSM amenities / transit / parks
# ══════════════════════════════════════════════

OVERPASS_QUERY_TEMPLATE = """
[out:json][timeout:25];
(
  node["amenity"](around:1000,{lat},{lng});
  node["railway"~"^(station|halt|tram_stop|subway_entrance)$"](around:2000,{lat},{lng});
  node["public_transport"="station"](around:2000,{lat},{lng});
  way["leisure"="park"](around:1500,{lat},{lng});
);
out center;
"""


async def fetch_osm_factors(client: httpx.AsyncClient, lat: float, lng: float) -> dict:
    """用 Overpass 一次查询拿到 3 个因子:amenity count / nearest transit / nearest park。
    429/504 时循环切换 mirror 并指数退避重试。"""
    query = OVERPASS_QUERY_TEMPLATE.format(lat=lat, lng=lng)
    elements = None
    for attempt in range(OVERPASS_RETRY_MAX):
        url = OVERPASS_MIRRORS[attempt % len(OVERPASS_MIRRORS)]
        try:
            r = await client.post(url, data={"data": query}, timeout=60.0)
            r.raise_for_status()
            elements = r.json().get("elements", [])
            break
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 504) and attempt < OVERPASS_RETRY_MAX - 1:
                wait = 2 ** (attempt + 2)  # 4s, 8s, 16s...
                print(f"    ⟳ Overpass {e.response.status_code} ({url.split('/')[2]}),"
                      f"{wait}s 后换 mirror 重试")
                await asyncio.sleep(wait)
                continue
            print(f"    ✗ Overpass 最终失败: {e}")
            return {}
        except Exception as e:
            print(f"    ✗ Overpass 异常: {e}")
            return {}
    if elements is None:
        return {}

    amenity_count = 0
    min_transit_m = None
    min_park_m = None

    for el in elements:
        # way 类型的 park 用 center 字段,node 用 lat/lng
        if "lat" in el and "lon" in el:
            elat, elng = el["lat"], el["lon"]
        elif "center" in el:
            elat, elng = el["center"]["lat"], el["center"]["lon"]
        else:
            continue

        dist = haversine_m(lat, lng, elat, elng)
        tags = el.get("tags", {})

        # 1km 内的 amenity 计数
        if "amenity" in tags and dist <= 1000:
            amenity_count += 1

        # 最近 transit(subway_entrance/station/halt/tram_stop)
        if (tags.get("railway") in ("station", "halt", "tram_stop", "subway_entrance")
                or tags.get("public_transport") == "station"):
            if min_transit_m is None or dist < min_transit_m:
                min_transit_m = dist

        # 最近 park
        if tags.get("leisure") == "park":
            if min_park_m is None or dist < min_park_m:
                min_park_m = dist

    return {
        "amenity_count_1km": amenity_count,
        "nearest_transit_m": int(min_transit_m) if min_transit_m is not None else None,
        "nearest_park_m": int(min_park_m) if min_park_m is not None else None,
    }


# ══════════════════════════════════════════════
# Walk Score API (可选)
# ══════════════════════════════════════════════

async def fetch_walk_score(
    client: httpx.AsyncClient, lat: float, lng: float, address: str
) -> dict:
    """Walk Score API 一次返回 walk/transit/bike 三个分数。"""
    if not WALKSCORE_ENABLED:
        return {}
    params = {
        "format": "json",
        "lat": lat,
        "lon": lng,
        "address": address,
        "transit": 1,  # 让 API 一并返回 transit score
        "bike": 1,     # 让 API 一并返回 bike score
        "wsapikey": WALKSCORE_KEY,
    }
    try:
        r = await client.get(WALKSCORE_URL, params=params, timeout=15.0)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"    ✗ Walk Score 失败: {e}")
        return {}

    out = {}
    if "walkscore" in data:
        out["walk_score"] = int(data["walkscore"])
    if data.get("transit", {}).get("score") is not None:
        out["transit_score"] = int(data["transit"]["score"])
    if data.get("bike", {}).get("score") is not None:
        out["bike_score"] = int(data["bike"]["score"])
    return out


# ══════════════════════════════════════════════
# Census Geocoder — 反向地理编码拿 tract_id
# ══════════════════════════════════════════════

async def fetch_tract_id(client: httpx.AsyncClient, lat: float, lng: float) -> Optional[str]:
    """把 lat/lng 反解成 11 位 tract GEOID(state+county+tract)。免费无 key。"""
    params = {
        "x": lng,
        "y": lat,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "layers": "Census Tracts",
        "format": "json",
    }
    try:
        r = await client.get(CENSUS_GEOCODER_URL, params=params, timeout=15.0)
        r.raise_for_status()
        tracts = r.json().get("result", {}).get("geographies", {}).get("Census Tracts", [])
        if tracts:
            return tracts[0].get("GEOID")
    except Exception as e:
        print(f"    ✗ Census Geocoder 失败: {e}")
    return None


# ══════════════════════════════════════════════
# FEMA NFHL — Flood zone
# ══════════════════════════════════════════════

async def fetch_flood_zone(client: httpx.AsyncClient, lat: float, lng: float) -> dict:
    """FEMA National Flood Hazard Layer — 查这个点是否在洪泛区。免费无 key。"""
    params = {
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "outFields": "FLD_ZONE",
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        r = await client.get(FEMA_NFHL_URL, params=params, timeout=15.0)
        r.raise_for_status()
        features = r.json().get("features", [])
        zone = features[0]["attributes"]["FLD_ZONE"] if features else "X"
        # "X" = 最小风险区,其他如 "A"/"AE"/"VE" = 高风险
        return {"flood_zone": zone}
    except Exception as e:
        print(f"    ✗ FEMA 失败: {e}")
        return {}


# ══════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--min-sold-date",
        type=str,
        default=None,
        help="只抓这个日期之后卖出的 listing(格式 YYYY-MM-DD)。"
             "不传则抓所有未完整采集的 listing。",
    )
    args = ap.parse_args()

    print("=" * 50)
    print("Edensign BI — Step 5: Location 因子采集")
    print("=" * 50)
    print(f"  Walk Score API: {'✓ 启用' if WALKSCORE_ENABLED else '✗ 未设置 key,跳过'}")
    print(f"  Overpass API:    ✓ 启用 (free, no key)")
    print(f"  FEMA NFHL:       ✓ 启用 (free, no key)")
    if args.min_sold_date:
        print(f"  Filter:         sold_date >= {args.min_sold_date}  (training-only 模式)")

    conn = await asyncpg.connect(DB_DSN)

    # 每条 listing 在 location_scores 里建立一条初始记录(若不存在)
    await conn.execute("""
        INSERT INTO location_scores (listing_id)
        SELECT l.listing_id FROM listings l
        WHERE NOT EXISTS (
          SELECT 1 FROM location_scores ls WHERE ls.listing_id = l.listing_id
        );
    """)

    # 构造过滤条件:可选只抓训练用到的 listing
    extra_filter = ""
    extra_params: list = []
    if args.min_sold_date:
        extra_filter = """
          AND lf.sold_price IS NOT NULL AND lf.sold_price > 0
          AND lf.sqft IS NOT NULL AND lf.sqft > 0
          AND lf.sold_date IS NOT NULL
          AND lf.sold_date >= $1::date
          AND lf.primary_style IS NOT NULL
          AND (l.data_quality_flag IS NULL
               OR l.data_quality_flag NOT IN (
                 'rental_leakage','no_interior_photos','bad_sqft','realtor_orphan'
               ))
        """
        from datetime import date as _date
        extra_params = [_date.fromisoformat(args.min_sold_date)]

    # 只拿还没采集齐的 listing(已有数据的跳过,增量补全)
    sql = f"""
        SELECT l.listing_id, l.address, l.lat, l.lng
        FROM listings l
        JOIN location_scores ls ON ls.listing_id = l.listing_id
        {"JOIN listing_full lf ON lf.listing_id = l.listing_id" if args.min_sold_date else ""}
        WHERE l.lat IS NOT NULL AND l.lng IS NOT NULL
          AND (ls.amenity_count_1km IS NULL OR ls.walk_score IS NULL)
          {extra_filter}
        ORDER BY l.listing_id
    """
    rows = await conn.fetch(sql, *extra_params)
    print(f"\n采集 {len(rows)} 条 listing 的 location 因子(已完整的跳过)...")
    if not rows:
        print("  全部 listing 已完整采集 ✓")
        await conn.close()
        return

    http = httpx.AsyncClient()
    success = 0

    for i, row in enumerate(rows, 1):
        listing_id = row["listing_id"]
        address = row["address"]
        lat, lng = row["lat"], row["lng"]
        print(f"  [{i}/{len(rows)}] {address[:50]}")

        # 并发调三个 API(FEMA NFHL layer 编号变了返回 404,暂时禁用;
        # 等扩展到沿海城市再修。Allston 内陆基本全是 X zone,影响不大)
        osm, walk, tract = await asyncio.gather(
            fetch_osm_factors(http, lat, lng),
            fetch_walk_score(http, lat, lng, address),
            fetch_tract_id(http, lat, lng),
        )
        combined = {**osm, **walk}
        if tract:
            # 只保留 census_tracts 表里实际存在的 tract_id(避免 FK 违反)
            exists = await conn.fetchval(
                "SELECT 1 FROM census_tracts WHERE tract_id = $1", tract
            )
            if exists:
                combined["tract_id"] = tract

        if not combined:
            print(f"    ✗ 所有因子采集失败")
            continue

        # 动态构造 UPDATE 语句(只更新实际拿到的字段)
        sets = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(combined.keys()))
        values = [listing_id, *combined.values()]
        await conn.execute(
            f"UPDATE location_scores SET {sets}, computed_at = NOW() WHERE listing_id = $1",
            *values,
        )

        parts = []
        if "tract_id" in combined:
            parts.append(f"tract={combined['tract_id'][-6:]}")  # 只显示后 6 位
        if "walk_score" in combined:
            parts.append(f"walk={combined['walk_score']}")
        if "transit_score" in combined:
            parts.append(f"transit={combined['transit_score']}")
        if "amenity_count_1km" in combined:
            parts.append(f"amenities={combined['amenity_count_1km']}")
        if "nearest_transit_m" in combined:
            parts.append(f"transit_dist={combined['nearest_transit_m']}m")
        if "nearest_park_m" in combined:
            parts.append(f"park_dist={combined['nearest_park_m']}m")
        if "flood_zone" in combined:
            parts.append(f"flood={combined['flood_zone']}")
        print(f"      → {' | '.join(parts)}")
        success += 1

        await asyncio.sleep(REQUEST_DELAY_SEC)

    await http.aclose()
    await conn.close()

    print("=" * 50)
    print(f"✅ 完成! 成功: {success}/{len(rows)}")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
