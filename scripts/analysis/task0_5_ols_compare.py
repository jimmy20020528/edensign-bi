#!/usr/bin/env python3
"""
Task 0.5 - A/B OLS smoke comparison with time-window contrast.

Runs 8 regressions:
  Version A (exclude EmptyRoom/Lived-in/Unclassified):
    - full sample
    - recent sample (sold_date >= 2020-01-01)
  Version B (keep EmptyRoom + Lived-in, exclude Unclassified; EmptyRoom baseline):
    - full sample
    - recent sample
for both outcomes:
  - log_psf
  - log_dom
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import asyncpg
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

_scripts = Path(__file__).resolve().parent.parent  # scripts/ root
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from db_dsn import get_db_dsn  # noqa: E402

MIN_ROWS_PER_STYLE = 3
RECENT_CUTOFF = date(2020, 1, 1)
BASE_DATE = date(2022, 1, 1)
TARGET_STYLE_KEYS = [
    "C(style_g)[T.Transitional]",
    "C(style_g)[T.Contemporary]",
    "C(style_g)[T.Modern Minimalist]",
    "C(style_g)[T.Scandinavian]",
    "C(style_g)[T.Lived-in]",
    "C(style_g)[T.Other]",
]


async def load_frame() -> pd.DataFrame:
    conn = await asyncpg.connect(get_db_dsn())
    rows = await conn.fetch(
        """
        SELECT
            lf.listing_id,
            lf.sold_price,
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
          AND (l.data_quality_flag IS NULL OR l.data_quality_flag NOT IN ('rental_leakage', 'no_interior_photos'))
        """
    )
    await conn.close()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    df["sold_date"] = pd.to_datetime(df["sold_date"]).dt.date
    return df


def _months_since_2022_q1(d: date) -> int:
    return (d.year - BASE_DATE.year) * 12 + (d.month - BASE_DATE.month)


def _prepare_common(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["months_since_2022_q1"] = out["sold_date"].apply(_months_since_2022_q1).astype(float)
    out["months_since_2022_q1_sq"] = out["months_since_2022_q1"] ** 2
    out["log_psf"] = np.log(out["price_per_sqft"].astype(float))
    out["bedrooms"] = out["bedrooms"].fillna(out["bedrooms"].median())
    out["bathrooms"] = out["bathrooms"].fillna(out["bathrooms"].median())
    out["year_built"] = out["year_built"].fillna(out["year_built"].median())
    out["walk_score"] = out["walk_score"].fillna(out["walk_score"].median())
    out["transit_score"] = out["transit_score"].fillna(out["transit_score"].median())
    out["amenity_count_1km"] = out["amenity_count_1km"].fillna(out["amenity_count_1km"].median())
    if out["median_income"].notna().any():
        out["median_income"] = out["median_income"].fillna(out["median_income"].median())
    else:
        out["median_income"] = 0.0
    out["dominant_archetype"] = out["dominant_archetype"].fillna("mixed")
    return out


def _collapse_styles(s: pd.Series) -> pd.Series:
    vc = s.value_counts()
    keep = set(vc[vc >= MIN_ROWS_PER_STYLE].index)
    return s.where(s.isin(keep), other="Other")


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


def _fit_psf(df: pd.DataFrame, version: str):
    has_baseline = (df["style_g"] == "Baseline_EmptyRoom").any()
    if version == "B" and has_baseline:
        formula = (
            "log_psf ~ sqft + bedrooms + bathrooms + year_built + walk_score + transit_score + "
            "amenity_count_1km + median_income + C(dominant_archetype) + "
            "months_since_2022_q1 + months_since_2022_q1_sq + "
            "C(style_g, Treatment(reference='Baseline_EmptyRoom'))"
        )
    else:
        formula = (
            "log_psf ~ sqft + bedrooms + bathrooms + year_built + walk_score + transit_score + "
            "amenity_count_1km + median_income + C(dominant_archetype) + "
            "months_since_2022_q1 + months_since_2022_q1_sq + C(style_g)"
        )
    return smf.ols(formula, data=df).fit(cov_type="HC3")


def _fit_dom(df: pd.DataFrame, version: str):
    dom = df[df["days_on_market"].notna() & (df["days_on_market"] > 0)].copy()
    if dom.empty:
        return None, dom
    dom["log_dom"] = np.log(dom["days_on_market"].astype(float))
    has_baseline = (dom["style_g"] == "Baseline_EmptyRoom").any()
    if version == "B" and has_baseline:
        formula = (
            "log_dom ~ sqft + bedrooms + bathrooms + year_built + walk_score + transit_score + "
            "amenity_count_1km + median_income + C(dominant_archetype) + "
            "months_since_2022_q1 + months_since_2022_q1_sq + "
            "C(style_g, Treatment(reference='Baseline_EmptyRoom'))"
        )
    else:
        formula = (
            "log_dom ~ sqft + bedrooms + bathrooms + year_built + walk_score + transit_score + "
            "amenity_count_1km + median_income + C(dominant_archetype) + "
            "months_since_2022_q1 + months_since_2022_q1_sq + C(style_g)"
        )
    return smf.ols(formula, data=dom).fit(cov_type="HC3"), dom


def _extract_top_style_terms(res) -> list[dict[str, Any]]:
    out = []
    if res is None:
        return out
    params = res.params
    pvals = res.pvalues
    cis = res.conf_int()
    for k in params.index:
        if not k.startswith("C(style_g"):
            continue
        out.append(
            {
                "term": k,
                "coef": float(params[k]),
                "p_value": float(pvals[k]),
                "ci_low": float(cis.loc[k, 0]),
                "ci_high": float(cis.loc[k, 1]),
            }
        )
    out.sort(key=lambda x: abs(x["coef"]), reverse=True)
    return out[:5]


def _find_term(res, name_hint: str) -> tuple[float | None, float | None]:
    if res is None:
        return None, None
    for k in res.params.index:
        if name_hint in k:
            return float(res.params[k]), float(res.pvalues[k])
    return None, None


def _walk_transit_from_result(res) -> dict[str, Any]:
    if res is None:
        return {"walk_score": None, "walk_p": None, "transit_score": None, "transit_p": None}
    wc, wp = _find_term(res, "walk_score")
    tc, tp = _find_term(res, "transit_score")
    return {
        "walk_score": wc,
        "walk_p": wp,
        "transit_score": tc,
        "transit_p": tp,
    }


def _cross_period_dom_compare(work: pd.DataFrame, version: str) -> dict[str, Any]:
    """Same log_dom spec as _fit_dom; compare walk/transit coefs with vs without cross_period rows."""
    res_all, dom_all = _fit_dom(work, version)
    res_no = None
    n_cp_in_dom = 0
    if res_all is not None and len(dom_all) and "data_quality_flag" in dom_all.columns:
        n_cp_in_dom = int((dom_all["data_quality_flag"] == "cross_period").sum())
        dom_no_cp = dom_all[dom_all["data_quality_flag"] != "cross_period"].copy()
        if len(dom_no_cp) >= 30:
            res_no, _ = _fit_dom(dom_no_cp, version)
    wt_all = _walk_transit_from_result(res_all)
    wt_no = _walk_transit_from_result(res_no)
    flip_walk = (
        wt_all["walk_score"] is not None
        and wt_no["walk_score"] is not None
        and (wt_all["walk_score"] > 0) != (wt_no["walk_score"] > 0)
    )
    flip_transit = (
        wt_all["transit_score"] is not None
        and wt_no["transit_score"] is not None
        and (wt_all["transit_score"] > 0) != (wt_no["transit_score"] > 0)
    )
    return {
        "n_dom": int(len(dom_all)) if res_all is not None else 0,
        "n_cross_period_in_dom": n_cp_in_dom,
        "with_cross_period": wt_all,
        "drop_cross_period": wt_no,
        "sign_flip_walk": flip_walk,
        "sign_flip_transit": flip_transit,
    }


def _count_style_psf_sig(res) -> tuple[int, list[str]]:
    """Count style-group dummies with p < 0.05 on log_psf model."""
    if res is None:
        return 0, []
    names: list[str] = []
    for k in res.params.index:
        if not k.startswith("C(style_g"):
            continue
        if float(res.pvalues[k]) < 0.05:
            names.append(k)
    return len(names), names


def _b_version_sign_sanity(res) -> dict[str, Any]:
    """Version B only: Modern/Scandi positive; Lived-in negative vs EmptyRoom baseline."""
    if res is None:
        return {"ok": False, "detail": "no_model"}
    out: dict[str, Any] = {"ok": True, "checks": []}

    def chk(label: str, coef: float | None, want: str) -> None:
        if coef is None:
            out["checks"].append({"label": label, "pass": False, "reason": "missing_term"})
            out["ok"] = False
            return
        if want == "positive" and coef <= 0:
            out["ok"] = False
            out["checks"].append({"label": label, "pass": False, "coef": coef})
        elif want == "negative" and coef >= 0:
            out["ok"] = False
            out["checks"].append({"label": label, "pass": False, "coef": coef})
        else:
            out["checks"].append({"label": label, "pass": True, "coef": coef})

    mm, _ = _find_term(res, "Modern Minimalist")
    sc, _ = _find_term(res, "Scandinavian")
    li, _ = _find_term(res, "Lived-in")
    chk("Modern Minimalist vs EmptyRoom", mm, "positive")
    chk("Scandinavian vs EmptyRoom", sc, "positive")
    chk("Lived-in vs EmptyRoom", li, "negative")
    return out


def _sign(x: float | None) -> str:
    if x is None:
        return "na"
    if x > 0:
        return "+"
    if x < 0:
        return "-"
    return "0"


def _run_suite(df: pd.DataFrame, version: str, window: str) -> dict[str, Any]:
    work = df.copy()
    if window == "recent":
        work = work[work["sold_date"] >= RECENT_CUTOFF].copy()
    work = _apply_version(work, version)
    if work.empty:
        return {"n": 0, "log_psf": None, "log_dom": None}

    res_psf = _fit_psf(work, version)
    res_dom, dom_df = _fit_dom(work, version)
    n_sig, sig_names = _count_style_psf_sig(res_psf)
    sign_sanity_b = _b_version_sign_sanity(res_psf) if version == "B" else None
    return {
        "n": int(len(work)),
        "n_dom": int(len(dom_df)),
        "log_psf": {
            "r2": float(res_psf.rsquared),
            "adj_r2": float(res_psf.rsquared_adj),
            "n_style_p_lt_005": n_sig,
            "style_terms_p_lt_005": sig_names,
            "top_style_terms": _extract_top_style_terms(res_psf),
            "summary": res_psf.summary().as_text(),
        },
        "log_psf_version_B_sign_sanity": sign_sanity_b,
        "log_dom": None
        if res_dom is None
        else {
            "r2": float(res_dom.rsquared),
            "adj_r2": float(res_dom.rsquared_adj),
            "top_style_terms": _extract_top_style_terms(res_dom),
            "summary": res_dom.summary().as_text(),
        },
        "cross_period_log_dom": _cross_period_dom_compare(work, version),
    }


def _build_sign_consistency(full: dict[str, Any], recent: dict[str, Any], outcome: str) -> list[dict[str, Any]]:
    rows = []
    full_terms = full.get(outcome, {}).get("top_style_terms", []) if full.get(outcome) else []
    recent_terms = recent.get(outcome, {}).get("top_style_terms", []) if recent.get(outcome) else []
    term_names = sorted({x["term"] for x in full_terms} | {x["term"] for x in recent_terms})
    for t in term_names:
        f_coef, _ = _find_term_obj(full_terms, t)
        r_coef, _ = _find_term_obj(recent_terms, t)
        rows.append(
            {
                "term": t,
                "full_coef": f_coef,
                "recent_coef": r_coef,
                "sign_consistent": _sign(f_coef) == _sign(r_coef) if f_coef is not None and r_coef is not None else False,
            }
        )
    return rows


def _find_term_obj(items: list[dict[str, Any]], term: str) -> tuple[float | None, float | None]:
    for it in items:
        if it["term"] == term:
            return it["coef"], it["p_value"]
    return None, None


def _build_verdict(results: dict[str, Any]) -> dict[str, Any]:
    b_full = results["version_B"]["full"]
    a_full = results["version_A"]["full"]
    n_b = int(b_full["log_psf"]["n_style_p_lt_005"])
    n_a = int(a_full["log_psf"]["n_style_p_lt_005"])
    cp_b = b_full["cross_period_log_dom"]
    cp_a = a_full["cross_period_log_dom"]
    flip_any = (
        cp_b["sign_flip_walk"]
        or cp_b["sign_flip_transit"]
        or cp_a["sign_flip_walk"]
        or cp_a["sign_flip_transit"]
    )
    sign_b_ok = bool((b_full.get("log_psf_version_B_sign_sanity") or {}).get("ok"))
    gate = n_b >= 2 or n_a >= 2
    recommend = "B"
    if n_a >= 3 and n_b < 2:
        recommend = "A"
    elif n_b >= n_a and b_full["n"] >= a_full["n"]:
        recommend = "B"
    exclude_dom_cp = bool(flip_any)
    line = (
        f"Verdict — Task 1: prefer Version {recommend} for log_psf / style lift (B full: n={b_full['n']}, "
        f"styles p<0.05 count={n_b}; A full: n={a_full['n']}, count={n_a}). "
        f"Version B coefficient sanity (Modern+, Scandinavian+, Lived-in− vs EmptyRoom): "
        f"{'PASS' if sign_b_ok else 'FAIL'}. "
        f"log_dom & cross_period: walk/transit coef sign flips when dropping cross_period "
        f"(A_full walk={cp_a['sign_flip_walk']} transit={cp_a['sign_flip_transit']}; "
        f"B_full walk={cp_b['sign_flip_walk']} transit={cp_b['sign_flip_transit']}) "
        f"→ {'exclude cross_period from log_dom training' if exclude_dom_cp else 'no sign-flip evidence; optional exclude for conservative DOM'}."
    )
    return {
        "log_psf_gate_at_least_2_style_p_lt_005": gate,
        "recommend_version_for_task1": recommend,
        "log_dom_exclude_cross_period": exclude_dom_cp,
        "version_B_sign_sanity_pass": sign_b_ok,
        "verdict_line": line,
    }


def main() -> None:
    df = asyncio.run(load_frame())
    if df.empty:
        raise SystemExit("No rows after hard excludes.")
    base = _prepare_common(df)

    results: dict[str, Any] = {}
    for ver in ("A", "B"):
        full = _run_suite(base, ver, "full")
        recent = _run_suite(base, ver, "recent")
        results[f"version_{ver}"] = {
            "full": full,
            "recent": recent,
            "sign_consistency_log_psf": _build_sign_consistency(full, recent, "log_psf"),
            "sign_consistency_log_dom": _build_sign_consistency(full, recent, "log_dom"),
        }

    results["verdict"] = _build_verdict(results)

    out_dir = Path(__file__).resolve().parent.parent / "models" / "baseline" / f"task0_5_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "task0_5_report.json"
    out_md = out_dir / "task0_5_report.md"
    out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    md = ["# Task 0.5 OLS A/B Report", ""]
    md.append("## Verdict (Task 1 entry)")
    md.append(results["verdict"]["verdict_line"])
    md.append("")
    for ver in ("A", "B"):
        b = results[f"version_{ver}"]
        md.append(f"## Version {ver}")
        for win in ("full", "recent"):
            part = b[win]
            md.append(f"### {win}")
            md.append(f"- n: {part['n']} (n_dom: {part['n_dom']})")
            if part["log_psf"]:
                lp = part["log_psf"]
                md.append(f"- log_psf R²: {lp['r2']:.4f}, adjR²: {lp['adj_r2']:.4f}")
                md.append(f"- log_psf style dummies p<0.05: {lp['n_style_p_lt_005']} {lp.get('style_terms_p_lt_005', [])}")
            if ver == "B" and part.get("log_psf_version_B_sign_sanity"):
                md.append(f"- Version B sign sanity: {part['log_psf_version_B_sign_sanity']}")
            if part["log_dom"]:
                md.append(f"- log_dom R²: {part['log_dom']['r2']:.4f}, adjR²: {part['log_dom']['adj_r2']:.4f}")
            cp = part.get("cross_period_log_dom") or {}
            md.append(
                f"- cross_period DOM sensitivity: n_cp_in_dom={cp.get('n_cross_period_in_dom')}, "
                f"walk(with)={cp.get('with_cross_period', {}).get('walk_score')} "
                f"walk(drop_cp)={cp.get('drop_cross_period', {}).get('walk_score')}, "
                f"transit(with)={cp.get('with_cross_period', {}).get('transit_score')} "
                f"transit(drop_cp)={cp.get('drop_cross_period', {}).get('transit_score')}, "
                f"sign_flip_walk={cp.get('sign_flip_walk')} sign_flip_transit={cp.get('sign_flip_transit')}"
            )
        md.append("")
        md.append("#### Sign consistency (log_psf)")
        for row in b["sign_consistency_log_psf"]:
            md.append(f"- {row['term']}: full={row['full_coef']}, recent={row['recent_coef']}, consistent={row['sign_consistent']}")
        md.append("")
        md.append("#### Sign consistency (log_dom)")
        for row in b["sign_consistency_log_dom"]:
            md.append(f"- {row['term']}: full={row['full_coef']}, recent={row['recent_coef']}, consistent={row['sign_consistent']}")
        md.append("")

    out_md.write_text("\n".join(md), encoding="utf-8")
    print(f"Saved: {out_json}")
    print(f"Saved: {out_md}")
    print(results["verdict"]["verdict_line"])


if __name__ == "__main__":
    main()

