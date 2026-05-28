from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import asyncpg
import joblib
import numpy as np
import pandas as pd

BASE_DATE = date(2022, 1, 1)
BI_ROOT = Path(__file__).resolve().parent.parent.parent
MODELS_BASELINE = BI_ROOT / "models" / "baseline"
DERIVED = BI_ROOT / "data" / "derived"

WARN_MODEL_DOM_LOW_CONFIDENCE = "model_dom_low_confidence"
WARN_SMALL_ZIP_LOW_SUPPORT = "small_zip_low_support"
WARN_LOW_SUPPORT = "low_support"
WARN_DATA_QUALITY_LIMITED = "data_quality_limited"


@dataclass
class StyleAggregate:
    style: str
    n: int
    median_dom: float | None
    median_ppsf: float | None
    avg_confidence: float | None


def _norm(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.5
    return (value - low) / (high - low)


def _sample_confidence(n: int) -> float:
    return 1.0 - math.exp(-n / 12.0)


def _overall_confidence_meta(overall_score: float, total_n: int) -> tuple[str, list[str]]:
    """Hard ZIP support floors + score bands. Warnings list may be attached only for model/hybrid."""
    w: list[str] = []
    if total_n < 80:
        w.append(WARN_SMALL_ZIP_LOW_SUPPORT)
    if total_n < 30:
        return "low", w
    if total_n < 80:
        return "medium", w
    if overall_score >= 0.75:
        return "high", w
    return "medium", w


_FEATURE_DISPLAY_NAMES: dict[str, str] = {
    "sqft":                    "Property Size (sqft)",
    "bedrooms":                "Bedrooms",
    "bathrooms":               "Bathrooms",
    "year_built":              "Year Built",
    "walk_score":              "Walkability Score",
    "walk_score_resid":        "Walkability vs Neighborhood Avg",
    "transit_score":           "Transit Score",
    "amenity_count_1km":       "Nearby Amenities (1km)",
    "median_income":           "Neighborhood Median Income",
    "months_since_2022_q1":    "Market Time Trend",
    "months_since_2022_q1_sq": "Market Time Trend²",
}


def _factor_display(name: str) -> str:
    if name in _FEATURE_DISPLAY_NAMES:
        return _FEATURE_DISPLAY_NAMES[name]
    if name.startswith("style_"):
        return f"Style: {name[6:]}"
    if name.startswith("arch_"):
        return f"Archetype: {name[5:]}"
    return name


def _all_drivers_psf(psf_b: dict[str, Any], Xrow: np.ndarray) -> dict[str, Any]:
    """
    Return all features split into boosters (positive contribution) and
    detractors (negative contribution). Each group is normalized to 100%
    independently so both lists are individually interpretable.
    """
    name = psf_b["best_model_name"]
    m = psf_b["models"][name]
    coef = np.ravel(m.coef_)
    names = psf_b["feature_names"]
    if len(coef) != len(names) or len(Xrow) != len(names):
        return {"boosters": [], "detractors": []}

    contribs = coef * Xrow

    pos = [(names[i], float(contribs[i])) for i in range(len(names)) if contribs[i] > 0]
    neg = [(names[i], float(contribs[i])) for i in range(len(names)) if contribs[i] < 0]

    pos_sum = sum(c for _, c in pos) or 1e-12
    neg_sum = sum(abs(c) for _, c in neg) or 1e-12

    boosters = [
        {"feature": n, "display_name": _factor_display(n), "pct": round(100.0 * c / pos_sum, 1)}
        for n, c in sorted(pos, key=lambda x: -x[1])
    ]
    detractors = [
        {"feature": n, "display_name": _factor_display(n), "pct": round(100.0 * abs(c) / neg_sum, 1)}
        for n, c in sorted(neg, key=lambda x: x[1])
    ]

    return {"boosters": boosters, "detractors": detractors}


def _style_confidence_object_for_model(score: float, n_listings: int) -> dict[str, Any]:
    sw: list[str] = []
    if n_listings < 5:
        sw.append(WARN_LOW_SUPPORT)
    return {"score": round(float(score), 4), "warnings": sw}


async def _fetch_style_support_recency(
    conn: asyncpg.Connection,
    zipcode: str,
    styles: list[str],
) -> dict[str, dict[str, int]]:
    if not styles:
        return {}
    rows = await conn.fetch(
        """
        SELECT
          primary_style,
          COUNT(*) FILTER (
            WHERE sold_date IS NOT NULL
              AND sold_date::date >= CURRENT_DATE - INTERVAL '3 months'
          )::int AS last_3mo,
          COUNT(*) FILTER (
            WHERE sold_date IS NOT NULL
              AND sold_date::date >= CURRENT_DATE - INTERVAL '1 year'
          )::int AS last_1yr,
          COUNT(*)::int AS all_time
        FROM listing_full
        WHERE zipcode = $1
          AND primary_style = ANY($2::text[])
          AND sold_price IS NOT NULL
        GROUP BY primary_style
        """,
        zipcode,
        styles,
    )
    return {
        r["primary_style"]: {
            "last_3mo": int(r["last_3mo"]),
            "last_1yr": int(r["last_1yr"]),
            "all_time": int(r["all_time"]),
        }
        for r in rows
    }


def _weights_for_objective(objective: str) -> dict[str, float]:
    table = {
        "balanced": {"speed": 0.45, "price": 0.45, "support": 0.10},
        "fast": {"speed": 0.70, "price": 0.20, "support": 0.10},
        "price": {"speed": 0.20, "price": 0.70, "support": 0.10},
    }
    return table.get(objective, table["balanced"])


def _months_since_2022_q1(d: date) -> float:
    return float((d.year - BASE_DATE.year) * 12 + (d.month - BASE_DATE.month))


def _latest_model_dir(prefix: str) -> Path:
    base = MODELS_BASELINE
    dirs = sorted([p for p in base.glob(f"{prefix}_*") if p.is_dir()], key=lambda p: p.name)
    if not dirs:
        raise FileNotFoundError(f"No {prefix}_* under {base}")
    return dirs[-1]


@lru_cache(maxsize=1)
def _load_training_parquet_path() -> Path:
    cands = sorted(DERIVED.glob("training_*.parquet"))
    if not cands:
        raise FileNotFoundError(f"No training_*.parquet under {DERIVED}")
    return cands[-1]


@lru_cache(maxsize=1)
def _primary_style_to_style_g() -> dict[str, str]:
    path = _load_training_parquet_path()
    df = pd.read_parquet(path, columns=["primary_style", "style_g"])
    m: dict[str, str] = {}
    for p, g in df.drop_duplicates(subset=["primary_style"]).itertuples(index=False):
        m[str(p)] = str(g)
    return m


@lru_cache(maxsize=2)
def _load_model_bundle(kind: str) -> dict[str, Any]:
    if kind == "psf":
        d = _latest_model_dir("log_psf_ridge")
    elif kind == "dom":
        d = _latest_model_dir("log_dom_ridge")
    else:
        raise ValueError(kind)
    return joblib.load(d / "model.pkl")


def _style_dummy_vector(style_cols: list[str], style_g: str) -> np.ndarray:
    v = np.zeros(len(style_cols), dtype=float)
    if style_g in ("Baseline_EmptyRoom", "EmptyRoom"):
        return v
    key = f"style_{style_g}"
    if key in style_cols:
        v[style_cols.index(key)] = 1.0
        return v
    other = "style_Other"
    if other in style_cols:
        v[style_cols.index(other)] = 1.0
    return v


def _arch_vector(bundle: dict[str, Any], archetype: str) -> np.ndarray:
    arch_cols: list[str] = bundle.get("arch_columns") or []
    if not arch_cols:
        return np.zeros(0, dtype=float)
    d = pd.DataFrame({"dominant_archetype": [archetype]})
    row = pd.get_dummies(d["dominant_archetype"], prefix="arch", drop_first=True)
    for c in arch_cols:
        if c not in row.columns:
            row[c] = 0
    return row.reindex(columns=arch_cols, fill_value=0).values.astype(float).ravel()


def _build_X_row(
    bundle: dict[str, Any],
    cont: dict[str, float],
    dominant_archetype: str,
    style_g: str,
) -> np.ndarray:
    C = bundle["continuous_cols"]
    raw = np.array([[cont[c] for c in C]], dtype=float)
    scaled = bundle["scaler"].transform(raw)[0]
    arch = _arch_vector(bundle, dominant_archetype)
    sty = _style_dummy_vector(bundle["style_columns"], style_g)
    return np.concatenate([scaled, arch, sty])


def _predict_log(bundle: dict[str, Any], Xrow: np.ndarray) -> float:
    name = bundle["best_model_name"]
    m = bundle["models"][name]
    return float(m.predict(Xrow.reshape(1, -1))[0])


async def _fetch_zip_median_profile(conn: asyncpg.Connection, zipcode: str) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        WITH z AS (
          SELECT
            lf.sqft,
            lf.bedrooms,
            lf.bathrooms,
            lf.year_built,
            lf.walk_score,
            lf.transit_score,
            lf.amenity_count_1km,
            lf.median_income,
            lf.dominant_archetype,
            lf.sold_date
          FROM listing_full lf
          WHERE lf.zipcode = $1
            AND lf.sold_price IS NOT NULL
            AND lf.sold_price > 0
            AND lf.sqft IS NOT NULL
            AND lf.sqft > 0
            AND lf.sold_date IS NOT NULL
        )
        SELECT
          percentile_cont(0.5) WITHIN GROUP (ORDER BY sqft) AS med_sqft,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY bedrooms) AS med_bedrooms,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY bathrooms) AS med_bathrooms,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY year_built) AS med_year_built,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY walk_score) AS med_walk,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY transit_score) AS med_transit,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY amenity_count_1km) AS med_amenity,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY median_income) AS med_income,
          percentile_disc(0.5) WITHIN GROUP (ORDER BY sold_date::date) AS med_sold_date
        FROM z
        """,
        zipcode,
    )
    mode_arch = await conn.fetchval(
        """
        SELECT dominant_archetype
        FROM listing_full lf
        WHERE lf.zipcode = $1
          AND lf.sold_price IS NOT NULL
          AND lf.sold_date IS NOT NULL
        GROUP BY dominant_archetype
        ORDER BY COUNT(*) DESC NULLS LAST
        LIMIT 1
        """,
        zipcode,
    )
    if row is None or row["med_sqft"] is None:
        return {}
    med_sd = row["med_sold_date"]
    if hasattr(med_sd, "date"):
        d = med_sd.date()
    else:
        d = med_sd
    ms = _months_since_2022_q1(d)
    def f64(x) -> float:
        return float(x) if x is not None else float("nan")

    return {
        "sqft": f64(row["med_sqft"]),
        "bedrooms": f64(row["med_bedrooms"]),
        "bathrooms": f64(row["med_bathrooms"]),
        "year_built": f64(row["med_year_built"]),
        "walk_score": f64(row["med_walk"]),
        "transit_score": f64(row["med_transit"]),
        "amenity_count_1km": f64(row["med_amenity"]),
        "median_income": f64(row["med_income"]),
        "months_since_2022_q1": ms,
        "months_since_2022_q1_sq": ms**2,
        "dominant_archetype": str(mode_arch or "mixed"),
    }


def _fill_cont_defaults(cont: dict[str, float]) -> dict[str, float]:
    out = dict(cont)
    for k, v in list(out.items()):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            out[k] = 0.0
    if out["bedrooms"] == 0.0:
        out["bedrooms"] = 2.0
    if out["bathrooms"] == 0.0:
        out["bathrooms"] = 1.0
    if out["year_built"] == 0.0:
        out["year_built"] = 1960.0
    if out["sqft"] == 0.0:
        out["sqft"] = 1000.0
    return out


def _heuristic_scores_for_results(
    styles: list[StyleAggregate],
    weights: dict[str, float],
) -> list[dict[str, Any]]:
    valid_dom = [s.median_dom for s in styles if s.median_dom is not None]
    valid_ppsf = [s.median_ppsf for s in styles if s.median_ppsf is not None]
    if valid_dom:
        dom_min, dom_max = min(valid_dom), max(valid_dom)
    else:
        dom_min, dom_max = 0.0, 1.0
    if valid_ppsf:
        ppsf_min, ppsf_max = min(valid_ppsf), max(valid_ppsf)
    else:
        ppsf_min, ppsf_max = 0.0, 1.0

    results: list[dict[str, Any]] = []
    for s in styles:
        speed = 1.0 - _norm(s.median_dom or dom_max, dom_min, dom_max) if valid_dom else 0.5
        price = _norm(s.median_ppsf or ppsf_min, ppsf_min, ppsf_max) if valid_ppsf else 0.5
        support = _sample_confidence(s.n)
        style_score = (
            weights["speed"] * speed + weights["price"] * price + weights["support"] * support
        )
        confidence = min(1.0, support * (s.avg_confidence or 0.7))
        results.append(
            {
                "style": s.style,
                "n_listings": s.n,
                "median_days_on_market": round(s.median_dom, 2) if s.median_dom is not None else None,
                "median_price_per_sqft": round(s.median_ppsf, 2) if s.median_ppsf is not None else None,
                "style_score": round(style_score, 4),
                "confidence": round(confidence, 4),
                "explain": {
                    "speed_component": round(speed, 4),
                    "price_component": round(price, 4),
                    "support_component": round(support, 4),
                },
            }
        )
    return results


def _model_scores_for_predictions(
    weights: dict[str, float],
    items: list[dict[str, Any]],
    pred_dom: list[float | None],
    pred_price: list[float | None],
) -> None:
    valid_dom = [d for d in pred_dom if d is not None and d > 0 and not math.isnan(d)]
    valid_price = [p for p in pred_price if p is not None and p > 0 and not math.isnan(p)]
    if valid_dom:
        dom_min, dom_max = min(valid_dom), max(valid_dom)
    else:
        dom_min, dom_max = 0.0, 1.0
    if valid_price:
        pr_min, pr_max = min(valid_price), max(valid_price)
    else:
        pr_min, pr_max = 0.0, 1.0

    for it, d, pr in zip(items, pred_dom, pred_price):
        support = it["explain"]["support_component"]
        if valid_dom and d is not None and d > 0 and not math.isnan(d):
            speed = 1.0 - _norm(d, dom_min, dom_max)
        else:
            speed = 0.5
        if valid_price and pr is not None and pr > 0 and not math.isnan(pr):
            price = _norm(pr, pr_min, pr_max)
        else:
            price = 0.5
        it["model_score"] = round(
            weights["speed"] * speed + weights["price"] * price + weights["support"] * support,
            4,
        )
        it["model_explain"] = {
            "speed_component": round(speed, 4),
            "price_component": round(price, 4),
            "support_component": round(support, 4),
        }


async def _enrich_model_or_hybrid(
    conn: asyncpg.Connection,
    base: dict[str, Any],
    zipcode: str,
    objective: str,
    scoring_mode: str,
) -> dict[str, Any]:
    weights = base["weights"]
    psf_b = _load_model_bundle("psf")
    dom_b = _load_model_bundle("dom")
    pmap = _primary_style_to_style_g()

    prof = await _fetch_zip_median_profile(conn, zipcode)
    if not prof:
        raise RuntimeError("Cannot build median ZIP profile for model scoring.")

    # Use today's date for the time feature so the model predicts "if listed now"
    # rather than extrapolating to the historical median sold_date (which falls
    # outside the training range and causes badly out-of-range predictions).
    ms_today = _months_since_2022_q1(date.today())
    prof["months_since_2022_q1"] = float(ms_today)
    prof["months_since_2022_q1_sq"] = float(ms_today ** 2)

    # Compute walk_score_resid using archetype means stored in the model bundle.
    if "walk_score_resid" in psf_b["continuous_cols"]:
        arch_walk_means: dict[str, float] = psf_b.get("archetype_walk_means") or {}
        arch = prof.get("dominant_archetype", "mixed")
        fallback = float(sum(arch_walk_means.values()) / len(arch_walk_means)) if arch_walk_means else 80.0
        arch_mean = arch_walk_means.get(arch, fallback)
        prof["walk_score_resid"] = prof.get("walk_score", arch_mean) - arch_mean

    cont = _fill_cont_defaults({k: prof[k] for k in psf_b["continuous_cols"]})
    arch = prof["dominant_archetype"]

    items = [dict(x) for x in base["all_styles"]]
    pred_prices: list[float | None] = []
    pred_doms: list[float | None] = []

    for it in items:
        st = it["style"]
        style_g = pmap.get(st, "Other")
        Xp = _build_X_row(psf_b, cont, arch, style_g)
        Xd = _build_X_row(dom_b, cont, arch, style_g)
        logp = _predict_log(psf_b, Xp)
        logd = _predict_log(dom_b, Xd)
        ppsf = math.exp(logp)
        price = ppsf * cont["sqft"]
        dom_days = math.exp(logd)
        it["model_predicted_price"] = round(price, 2)
        it["model_predicted_price_per_sqft"] = round(ppsf, 2)
        it["model_predicted_days_on_market"] = round(dom_days, 2)
        pred_prices.append(price)
        pred_doms.append(dom_days)

    _model_scores_for_predictions(weights, items, pred_doms, pred_prices)

    if scoring_mode == "model":
        items.sort(key=lambda x: x["model_score"], reverse=True)
    elif scoring_mode == "hybrid":
        for it in items:
            it["hybrid_score"] = round(0.5 * it["style_score"] + 0.5 * it["model_score"], 4)
        items.sort(key=lambda x: x["hybrid_score"], reverse=True)

    for it in items:
        it["confidence"] = _style_confidence_object_for_model(
            float(it["confidence"]), int(it["n_listings"])
        )

    total_n = base["confidence"]["n_listings"]
    overall_score = float(base["confidence"]["overall_score"])
    overall_label, zip_warns = _overall_confidence_meta(overall_score, total_n)

    top_styles = [it["style"] for it in items[:3]]
    recency = await _fetch_style_support_recency(conn, zipcode, top_styles)

    recommended = items[:3]
    for it in recommended:
        style_g = pmap.get(it["style"], "Other")
        Xp = _build_X_row(psf_b, cont, arch, style_g)
        sup = recency.get(it["style"], {"last_3mo": 0, "last_1yr": 0, "all_time": it["n_listings"]})
        it["evidence"] = {
            "top_drivers": _all_drivers_psf(psf_b, Xp),
            "support_count": int(it["n_listings"]),
            "support_recency": {
                "last_3mo": int(sup.get("last_3mo", 0)),
                "last_1yr": int(sup.get("last_1yr", 0)),
                "all_time": int(sup.get("all_time", it["n_listings"])),
            },
        }

    warnings: list[str] = []
    warnings.extend(zip_warns)
    warnings.append(WARN_MODEL_DOM_LOW_CONFIDENCE)
    warnings.append(WARN_DATA_QUALITY_LIMITED)

    out = dict(base)
    out["scoring_mode"] = scoring_mode
    out["warnings"] = warnings
    out["recommended_styles"] = recommended
    out["all_styles"] = items
    out["confidence"] = {
        **base["confidence"],
        "overall": overall_label,
    }
    out["model_meta"] = {
        "log_psf_artifact": str(_latest_model_dir("log_psf_ridge")),
        "log_dom_artifact": str(_latest_model_dir("log_dom_ridge")),
        "zip_median_profile": {k: round(v, 4) if isinstance(v, float) else v for k, v in prof.items()},
    }
    notes = list(out["methodology"]["notes"])
    notes.append(
        f"scoring_mode={scoring_mode}: counterfactual Ridge/Lasso baselines vs ZIP median listing; "
        "EmptyRoom reference in training."
    )
    out["methodology"] = {**out["methodology"], "notes": notes}
    return out


async def analyze_zipcode(
    conn: asyncpg.Connection,
    zipcode: str,
    objective: str = "balanced",
    scoring_mode: str = "heuristic",
) -> dict[str, Any]:
    scoring_mode = scoring_mode.lower().strip()
    objective = objective.lower().strip()
    weights = _weights_for_objective(objective)

    rows = await conn.fetch(
        """
        WITH base AS (
          SELECT
            primary_style,
            days_on_market,
            price_per_sqft,
            style_confidence
          FROM listing_full
          WHERE zipcode = $1
            AND primary_style IS NOT NULL
            AND primary_style NOT IN ('Unclassified', 'EmptyRoom', 'Lived-in')
            AND sold_price IS NOT NULL
        )
        SELECT
          primary_style,
          COUNT(*)::int AS n,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY days_on_market)
            FILTER (WHERE days_on_market IS NOT NULL AND days_on_market > 0) AS median_dom,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY price_per_sqft)
            FILTER (WHERE price_per_sqft IS NOT NULL AND price_per_sqft > 0) AS median_ppsf,
          AVG(style_confidence) AS avg_confidence
        FROM base
        GROUP BY primary_style
        HAVING COUNT(*) >= 3
        ORDER BY n DESC
        """,
        zipcode,
    )

    if not rows:
        insufficient = {
            "zipcode": zipcode,
            "objective": objective,
            "weights": weights,
            "status": "insufficient_data",
            "message": "No enough sold listings in last 365 days for this ZIP code.",
            "recommended_styles": [],
            "confidence": {"overall": "low", "n_listings": 0, "style_count": 0},
        }
        return insufficient

    styles: list[StyleAggregate] = [
        StyleAggregate(
            style=r["primary_style"],
            n=r["n"],
            median_dom=float(r["median_dom"]) if r["median_dom"] is not None else None,
            median_ppsf=float(r["median_ppsf"]) if r["median_ppsf"] is not None else None,
            avg_confidence=float(r["avg_confidence"]) if r["avg_confidence"] is not None else None,
        )
        for r in rows
    ]

    results = _heuristic_scores_for_results(styles, weights)
    results.sort(key=lambda x: x["style_score"], reverse=True)
    top3 = results[:3]
    total_n = sum(s.n for s in styles)
    overall_conf = _sample_confidence(total_n) * min(1.0, len(styles) / 6.0)
    overall_label, _ = _overall_confidence_meta(overall_conf, total_n)

    heuristic_payload: dict[str, Any] = {
        "zipcode": zipcode,
        "objective": objective,
        "weights": weights,
        "status": "ok",
        "recommended_styles": top3,
        "all_styles": results,
        "confidence": {
            "overall": overall_label,
            "overall_score": round(overall_conf, 4),
            "n_listings": total_n,
            "style_count": len(styles),
        },
        "methodology": {
            "objective": "Rank styles that may sell faster and at higher price per sqft.",
            "formula": "score = w_speed*speed + w_price*price + w_support*support",
            "notes": [
                "Uses all historical sold listings in this ZIP (2022-2024).",
                "This is descriptive scoring MVP, not causal inference.",
                "sale_to_list_ratio is excluded due to current list_price data quality issue.",
                "objective supports: balanced, fast, price.",
                "Styles with fewer than 3 sold listings are filtered out.",
            ],
        },
    }

    if scoring_mode == "heuristic":
        return heuristic_payload

    if scoring_mode not in {"model", "hybrid"}:
        raise ValueError(f"Invalid scoring_mode: {scoring_mode}")

    return await _enrich_model_or_hybrid(conn, heuristic_payload, zipcode, objective, scoring_mode)
