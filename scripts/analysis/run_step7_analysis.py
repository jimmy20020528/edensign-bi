#!/usr/bin/env python3
"""
Step 7 (MVP): 控制房屋与区位因子后,看 primary_style 与成交价 / 单价 / DOM 的关联。

用法(在 bi/ 目录):
    source .venv/bin/activate
    python scripts/run_step7_analysis.py

说明:
  - 数据来自视图 listing_full (listings + style + location + tract)。
  - 样本少(n≈29)时系数方差极大,输出仅供内部探索,不作显著性背书。
  - sale_to_list 在 list_price 缺失时不可靠,本脚本若检测到 list≈sold 会提示。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

_scripts = Path(__file__).resolve().parent.parent  # scripts/ root
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

import asyncpg  # noqa: E402

from db_dsn import get_db_dsn  # noqa: E402

MIN_ROWS_PER_STYLE = 3
PAST_YEAR_CUTOFF = date(2025, 5, 1)
BASE_DATE = date(2022, 1, 1)


async def load_frame(min_sold_date: date | None = None) -> pd.DataFrame:
    try:
        conn = await asyncpg.connect(get_db_dsn())
    except asyncpg.InvalidPasswordError as e:
        raise SystemExit(
            "数据库登录失败: InvalidPasswordError\n"
            "  → 请把 bi/.env 里的 DB_PASSWORD 改成与 Postgres 实际密码一致。\n"
            "  → 若用「docker compose up -d」起库，密码在 docker-compose.yml 的 POSTGRES_PASSWORD，"
            "默认是 edensign_dev。\n"
            "  → 若 .env 里仍是占位符 your_password_here，也会连不上。"
        ) from e
    except (ConnectionRefusedError, OSError) as e:
        raise SystemExit(
            "连不上数据库 (连接被拒绝)。请先在本机启动 Postgres，例如:\n"
            "  cd bi && docker compose up -d\n"
            "不要把「# 注释」一并粘贴进终端，否则会出现: no such service: #"
        ) from e
    sql = """
        SELECT
            lf.listing_id,
            lf.sold_price,
            lf.list_price,
            lf.sqft,
            lf.bedrooms,
            lf.bathrooms,
            lf.year_built,
            lf.days_on_market,
            lf.primary_style,
            lf.style_confidence,
            lf.walk_score,
            lf.transit_score,
            lf.bike_score,
            lf.amenity_count_1km,
            lf.median_income,
            lf.median_age,
            lf.dominant_archetype,
            lf.sold_date,
            lf.price_per_sqft,
            lf.sale_to_list_ratio,
            l.data_quality_flag
        FROM listing_full lf
        JOIN listings l USING(listing_id)
        WHERE lf.sold_price IS NOT NULL AND lf.sold_price > 0
          AND lf.sqft IS NOT NULL AND lf.sqft > 0
          AND lf.primary_style IS NOT NULL
          AND (l.data_quality_flag IS NULL OR l.data_quality_flag NOT IN ('rental_leakage', 'no_interior_photos'))
    """
    params: list[Any] = []
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


def _collapse_styles(s: pd.Series) -> pd.Series:
    vc = s.value_counts()
    keep = set(vc[vc >= MIN_ROWS_PER_STYLE].index)
    return s.where(s.isin(keep), other="Other")


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["months_since_2022_q1"] = out["sold_date"].apply(
        lambda d: (d.year - BASE_DATE.year) * 12 + (d.month - BASE_DATE.month)
    ).astype(float)
    out["months_since_2022_q1_sq"] = out["months_since_2022_q1"] ** 2
    out["log_psf"] = np.log(out["price_per_sqft"].astype(float))
    def fill_median_or_zero(col: str) -> None:
        if out[col].notna().any():
            out[col] = out[col].fillna(out[col].median())
        else:
            out[col] = 0.0

    fill_median_or_zero("year_built")
    fill_median_or_zero("bedrooms")
    fill_median_or_zero("bathrooms")
    for col in ("walk_score", "transit_score", "amenity_count_1km", "median_income"):
        fill_median_or_zero(col)
    out["dominant_archetype"] = out["dominant_archetype"].fillna("mixed")
    return out


def _warn_list_price(df: pd.DataFrame) -> None:
    if df.empty:
        return
    same = (df["list_price"] == df["sold_price"]) | (
        (df["list_price"].notna())
        & (df["sold_price"].notna())
        & (np.abs(df["list_price"] - df["sold_price"]) < 1)
    )
    pct = 100.0 * same.sum() / len(df)
    if pct > 50:
        print(
            f"\n⚠  有 {pct:.0f}% 行的 list_price ≈ sold_price(Redfin GIS 常缺原始挂牌价),"
            " sale_to_list / 降价类指标勿信。\n"
        )


def _fit(name: str, formula: str, data: pd.DataFrame):
    print(f"\n{'=' * 80}\n模型: {name}\n公式: {formula}\n{'=' * 80}")
    try:
        res = smf.ols(formula, data=data).fit(cov_type="HC3")
        print(f"R²={res.rsquared:.4f}, Adj R²={res.rsquared_adj:.4f}, n={len(data)}")
        return res
    except Exception as e:
        print(f"拟合失败: {e}")
        return None


def _apply_version(df: pd.DataFrame, version: str) -> pd.DataFrame:
    out = df.copy()
    if version == "A":
        out = out[~out["primary_style"].isin(["EmptyRoom", "Lived-in", "Unclassified"])].copy()
        out["style_g"] = _collapse_styles(out["primary_style"])
    else:
        out = out[out["primary_style"] != "Unclassified"].copy()
        out["style_g"] = _collapse_styles(out["primary_style"])
        out["style_g"] = out["style_g"].replace({"EmptyRoom": "Baseline_EmptyRoom"})
        out["style_g"] = out["style_g"].fillna("Other")
    return out


def _top_style_terms(res, topn: int = 8) -> list[dict[str, float | str]]:
    if res is None:
        return []
    rows: list[dict[str, float | str]] = []
    ci = res.conf_int()
    for k in res.params.index:
        if not k.startswith("C(style_g"):
            continue
        rows.append(
            {
                "term": k,
                "coef": float(res.params[k]),
                "p_value": float(res.pvalues[k]),
                "ci_low": float(ci.loc[k, 0]),
                "ci_high": float(ci.loc[k, 1]),
            }
        )
    rows.sort(key=lambda x: abs(float(x["coef"])), reverse=True)
    return rows[:topn]


def _summarize_one(version: str, frame: pd.DataFrame) -> dict[str, Any]:
    model_psf = _fit(
        f"Version {version} | log_psf",
        "log_psf ~ sqft + bedrooms + bathrooms + year_built + "
        "walk_score + transit_score + amenity_count_1km + median_income + "
        "months_since_2022_q1 + months_since_2022_q1_sq + C(style_g)",
        frame,
    )
    dom = frame[frame["days_on_market"].notna() & (frame["days_on_market"] > 0)].copy()
    model_dom = None
    if len(dom) >= 12:
        dom["log_dom"] = np.log(dom["days_on_market"].astype(float))
        model_dom = _fit(
            f"Version {version} | log_dom",
            "log_dom ~ sqft + bedrooms + bathrooms + year_built + "
            "walk_score + transit_score + amenity_count_1km + median_income + "
            "months_since_2022_q1 + months_since_2022_q1_sq + C(style_g)",
            dom,
        )
    return {
        "version": version,
        "n_psf": int(len(frame)),
        "n_dom": int(len(dom)),
        "log_psf_r2": None if model_psf is None else float(model_psf.rsquared),
        "log_dom_r2": None if model_dom is None else float(model_dom.rsquared),
        "log_psf_top_style_terms": _top_style_terms(model_psf),
        "log_dom_top_style_terms": _top_style_terms(model_dom),
        "log_psf_sig_count_p_lt_005": sum(
            1 for x in _top_style_terms(model_psf, topn=100) if float(x["p_value"]) < 0.05
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--past-year-only",
        action="store_true",
        help="Use sold_date >= 2025-05-01 subset only.",
    )
    args = ap.parse_args()

    print("Edensign BI — Step 7 OLS smoke test (Version A/B)\n")
    min_sold_date = PAST_YEAR_CUTOFF if args.past_year_only else None
    df = asyncio.run(load_frame(min_sold_date=min_sold_date))
    if df.empty:
        print("listing_full 无可用行: 检查数据库是否已灌入 listings / style / location。")
        return

    print(f"样本量 n = {len(df)} (有成交价、面积、且已分类风格的 listing)")
    if min_sold_date:
        print(f"时间过滤: sold_date >= {min_sold_date.isoformat()}")
    _warn_list_price(df)
    df = _prepare(df)
    out: dict[str, Any] = {
        "past_year_only": bool(args.past_year_only),
        "min_sold_date": None if min_sold_date is None else min_sold_date.isoformat(),
        "total_n_after_filters": int(len(df)),
        "results": [],
    }
    for version in ("A", "B"):
        sub = _apply_version(df, version)
        print(f"\nVersion {version} style count:")
        print(sub["style_g"].value_counts().to_string())
        out["results"].append(_summarize_one(version, sub))

    print("\nTop style terms (log_psf) with p-values:")
    for r in out["results"]:
        print(f"\nVersion {r['version']} | n_psf={r['n_psf']} | R²={r['log_psf_r2']}")
        for t in r["log_psf_top_style_terms"][:6]:
            print(f"  {t['term']}: coef={t['coef']:.4f}, p={t['p_value']:.4g}")

    out_dir = Path(__file__).resolve().parent.parent / "models" / "baseline"
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"step7_smoke_{ts}.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nSaved smoke report: {out_path}")


if __name__ == "__main__":
    main()
