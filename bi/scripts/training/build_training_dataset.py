#!/usr/bin/env python3
"""
Task 1 — Build Version B training table (EmptyRoom baseline via style_g, Lived-in kept).

Writes bi/data/derived/training_<timestamp>.parquet with raw categoricals + numerics;
training masks distinguish log_psf vs log_dom (dom excludes cross_period).
"""

from __future__ import annotations

import asyncio
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import asyncpg
import numpy as np
import pandas as pd

_scripts = Path(__file__).resolve().parent.parent  # scripts/ root
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from db_dsn import get_db_dsn  # noqa: E402

MIN_ROWS_PER_STYLE = 3
# Rolling 365-day training window: always the past year from today
RECENT_CUTOFF = date.today() - timedelta(days=365)
BI_ROOT = Path(__file__).resolve().parent.parent
DERIVED = BI_ROOT / "data" / "derived"

# Quality filter thresholds — removes multifamily buildings and impossible values
SQFT_MIN = 200
SQFT_MAX = 3500   # >3500 sqft = likely multifamily rental building
BEDROOMS_MAX = 8  # 9+ bedrooms = multifamily building
PSF_MIN = 250     # <$250/sqft impossible for Boston residential


def _collapse_styles(s: pd.Series) -> pd.Series:
    vc = s.value_counts()
    keep = set(vc[vc >= MIN_ROWS_PER_STYLE].index)
    return s.where(s.isin(keep), other="Other")


def _apply_quality_filters(df: pd.DataFrame) -> pd.DataFrame:
    n_before = len(df)
    out = df[
        df["sqft"].between(SQFT_MIN, SQFT_MAX)
        & (df["bedrooms"] <= BEDROOMS_MAX)
        & (pd.to_numeric(df["price_per_sqft"], errors="coerce") >= PSF_MIN)
    ].copy()
    removed = n_before - len(out)
    if removed:
        print(f"quality_filter: removed {removed} rows (multifamily/impossible values) → {len(out)} remain")
    return out


def _apply_version_b(df: pd.DataFrame) -> pd.DataFrame:
    out = df[df["primary_style"] != "Unclassified"].copy()
    out["style_g"] = _collapse_styles(out["primary_style"])
    out["style_g"] = out["style_g"].replace({"EmptyRoom": "Baseline_EmptyRoom"})
    out["style_g"] = out["style_g"].fillna("Other")
    return out


def _prepare_numeric(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["log_psf"] = np.log(out["price_per_sqft"].astype(float))
    def fill_median_or_zero(col: str) -> None:
        if out[col].notna().any():
            out[col] = out[col].fillna(out[col].median())
        else:
            out[col] = 0.0

    for col in (
        "bedrooms",
        "bathrooms",
        "year_built",
        "walk_score",
        "transit_score",
        "amenity_count_1km",
        "median_income",
    ):
        fill_median_or_zero(col)
    out["dominant_archetype"] = out["dominant_archetype"].fillna("mixed")
    # walk_score residual: remove archetype-level mean so the feature is orthogonal
    # to archetype dummies and only captures within-archetype walkability deviation.
    arch_mean_walk = out.groupby("dominant_archetype")["walk_score"].transform("mean")
    out["walk_score_resid"] = out["walk_score"] - arch_mean_walk
    dom_ok = out["days_on_market"].notna() & (out["days_on_market"] > 0)
    out["log_dom"] = np.nan
    out.loc[dom_ok, "log_dom"] = np.log(out.loc[dom_ok, "days_on_market"].astype(float))
    return out


async def load_frame(min_sold_date: date | None = None) -> pd.DataFrame:
    conn = await asyncpg.connect(get_db_dsn())
    sql = """
        SELECT
            lf.listing_id,
            lf.sqft,
            lf.bedrooms,
            lf.bathrooms,
            lf.year_built,
            lf.days_on_market,
            lf.sold_date,
            lf.primary_style,
            lf.walk_score,
            lf.transit_score,
            lf.amenity_count_1km,
            lf.median_income,
            lf.dominant_archetype,
            lf.price_per_sqft,
            l.data_quality_flag
        FROM listing_full lf
        JOIN listings l ON l.listing_id = lf.listing_id
        WHERE lf.sold_price IS NOT NULL
          AND lf.sold_price > 0
          AND lf.sqft IS NOT NULL
          AND lf.sqft > 0
          AND lf.sold_date IS NOT NULL
          AND lf.primary_style IS NOT NULL
          AND (l.data_quality_flag IS NULL OR l.data_quality_flag NOT IN ('rental_leakage', 'no_interior_photos', 'bad_sqft', 'realtor_orphan'))
    """
    params: list[object] = []
    if min_sold_date is not None:
        sql += "\n  AND lf.sold_date >= $1::date"
        params.append(min_sold_date)
    rows = await conn.fetch(sql, *params)
    await conn.close()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    df["sold_date"] = pd.to_datetime(df["sold_date"]).dt.date
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--min-sold-date",
        type=str,
        default=None,
        help="Optional lower bound sold_date (YYYY-MM-DD).",
    )
    args = ap.parse_args()

    min_sold_date: date | None = None
    if args.min_sold_date:
        min_sold_date = date.fromisoformat(args.min_sold_date)

    df = asyncio.run(load_frame(min_sold_date=min_sold_date))
    if df.empty:
        raise SystemExit("No rows after SQL filters.")
    df = _apply_quality_filters(df)
    if df.empty:
        raise SystemExit("No rows after quality filters.")
    df = _apply_version_b(df)
    df = _prepare_numeric(df)
    flag = df["data_quality_flag"].fillna("clean")
    df["eligible_log_psf"] = df["sold_date"] >= RECENT_CUTOFF
    df["eligible_log_dom"] = df["eligible_log_psf"] & df["log_dom"].notna() & (flag != "cross_period")

    DERIVED.mkdir(parents=True, exist_ok=True)
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    path = DERIVED / f"training_{ts}.parquet"
    df.to_parquet(path, index=False)
    print(path)
    print(
        "counts:",
        "n_psf=", int(df["eligible_log_psf"].sum()),
        "n_dom=", int(df["eligible_log_dom"].sum()),
    )


if __name__ == "__main__":
    main()
