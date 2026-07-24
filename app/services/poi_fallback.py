from __future__ import annotations

"""POI fallback providers — used ONLY when every Overpass mirror failed.

Two independent, OSM-data-backed hosted APIs (so results look the same as the
Overpass path), tried in order:
  1. Geoapify Places   (GEOAPIFY_API_KEY,   free 3000 credits/day)
  2. LocationIQ Nearby (LOCATIONIQ_API_KEY, free 5000 requests/day)

A provider without its key configured is skipped, so with no keys this module
is inert and fetch_pois behaves exactly as before (returns {}).

Output shape matches neighborhood_data.fetch_pois:
  {category: [{name, kind, distance_mi}, ...], "_total": n}
Category buckets mirror neighborhood_data._POI_CATEGORIES — keep in sync.
"""

import logging
import math
import os

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 12.0


def _haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _bucketize(items: list[tuple[str, str, str, float]], per_category: int) -> dict:
    """items: (category, name, kind, distance_mi) → fetch_pois-shaped dict."""
    buckets: dict[str, list[dict]] = {}
    seen: set[tuple[str, str]] = set()
    for cat, name, kind, dist in items:
        dedup = (cat, name.lower())
        if dedup in seen:
            continue
        seen.add(dedup)
        buckets.setdefault(cat, []).append(
            {"name": name, "kind": kind, "distance_mi": round(dist, 2)}
        )
    result: dict = {}
    total = 0
    for cat, rows in buckets.items():
        rows.sort(key=lambda x: x["distance_mi"])
        result[cat] = rows[:per_category]
        total += len(result[cat])
    if total:
        result["_total"] = total
    return result


# ---- Geoapify: category-slug prefix → (our bucket, kind) ----
_GEOAPIFY_CATEGORIES = (
    "catering.restaurant,catering.cafe,catering.fast_food,leisure.park,"
    "commercial.supermarket,commercial.convenience,sport.fitness,"
    "education.school,commercial.shopping_mall,commercial.department_store,"
    "public_transport"
)
_GEOAPIFY_MAP = [  # first prefix match wins
    ("catering.fast_food", ("dining", "fast_food")),
    ("catering.cafe", ("dining", "cafe")),
    ("catering.restaurant", ("dining", "restaurant")),
    ("leisure.park", ("recreation", "park")),
    ("commercial.supermarket", ("grocery", "supermarket")),
    ("commercial.convenience", ("grocery", "convenience")),
    ("sport.fitness", ("fitness", "fitness_centre")),
    ("education.school", ("schools", "school")),
    ("commercial.shopping_mall", ("shopping", "mall")),
    ("commercial.department_store", ("shopping", "department_store")),
    ("public_transport", ("transit", "station")),
]


def _geoapify(lat: float, lon: float, radius_m: int, per_category: int) -> dict:
    api_key = os.environ.get("GEOAPIFY_API_KEY")
    if not api_key:
        return {}
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.get("https://api.geoapify.com/v2/places", params={
            "categories": _GEOAPIFY_CATEGORIES,
            "filter": f"circle:{lon},{lat},{radius_m}",
            "limit": 200,
            "apiKey": api_key,
        })
        r.raise_for_status()
        feats = r.json().get("features", [])
    items = []
    for f in feats:
        p = f.get("properties", {})
        name = p.get("name")
        e_lat, e_lon = p.get("lat"), p.get("lon")
        if not name or e_lat is None or e_lon is None:
            continue
        cats = p.get("categories") or []
        for prefix, (bucket, kind) in _GEOAPIFY_MAP:
            if any(c == prefix or c.startswith(prefix + ".") for c in cats):
                items.append((bucket, name, kind,
                              _haversine_mi(lat, lon, float(e_lat), float(e_lon))))
                break
    return _bucketize(items, per_category)


# ---- LocationIQ: response class/type ARE osm key/value — same map as Overpass ----
_LOCATIONIQ_TAGS = (
    "restaurant,cafe,fast_food,park,supermarket,convenience,fitness_centre,"
    "school,mall,department_store,bus_station,station"
)
_LOCATIONIQ_MAP = {  # osm type value → (our bucket, kind)
    "restaurant": ("dining", "restaurant"),
    "cafe": ("dining", "cafe"),
    "fast_food": ("dining", "fast_food"),
    "park": ("recreation", "park"),
    "supermarket": ("grocery", "supermarket"),
    "convenience": ("grocery", "convenience"),
    "fitness_centre": ("fitness", "fitness_centre"),
    "school": ("schools", "school"),
    "mall": ("shopping", "mall"),
    "department_store": ("shopping", "department_store"),
    "bus_station": ("transit", "bus_station"),
    "station": ("transit", "station"),
}


def _locationiq(lat: float, lon: float, radius_m: int, per_category: int) -> dict:
    api_key = os.environ.get("LOCATIONIQ_API_KEY")
    if not api_key:
        return {}
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.get("https://us1.locationiq.com/v1/nearby", params={
            "key": api_key, "lat": lat, "lon": lon,
            "radius": min(radius_m, 30000), "tag": _LOCATIONIQ_TAGS,
            "limit": 50, "format": "json",
        })
        r.raise_for_status()
        rows = r.json()
    items = []
    for it in rows if isinstance(rows, list) else []:
        name = it.get("name")
        mapped = _LOCATIONIQ_MAP.get(it.get("type", ""))
        if not name or not mapped:
            continue
        dist_m = it.get("distance")
        if dist_m is not None:
            dist = float(dist_m) / 1609.344
        elif it.get("lat") and it.get("lon"):
            dist = _haversine_mi(lat, lon, float(it["lat"]), float(it["lon"]))
        else:
            continue
        items.append((mapped[0], name, mapped[1], dist))
    return _bucketize(items, per_category)


def fetch_pois_fallback(lat: float, lon: float, radius_m: int = 4000,
                        per_category: int = 6) -> dict:
    """Try each configured provider in order; first non-empty result wins."""
    for provider in (_geoapify, _locationiq):
        try:
            buckets = provider(lat, lon, radius_m, per_category)
        except Exception as exc:
            logger.warning("Neighborhood: POI fallback %s failed: %s",
                           provider.__name__, exc)
            continue
        if buckets.get("_total"):
            logger.info("Neighborhood: amenities served by fallback %s", provider.__name__)
            return buckets
    return {}
