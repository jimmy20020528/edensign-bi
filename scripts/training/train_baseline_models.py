#!/usr/bin/env python3
"""
Task 2 — OLS vs Ridge vs Lasso with LOO-CV; train log_psf and log_dom (Version B artifacts).

Reads training_*.parquet from Task 1. Writes per target:
  bi/models/baseline/log_{psf,dom}_ridge_<ts>/{model.pkl, eval.json, eval.md}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import Lasso, LinearRegression, Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import LeaveOneOut
from sklearn.preprocessing import StandardScaler

BI_ROOT = Path(__file__).resolve().parent.parent
MODELS_BASE = BI_ROOT / "models" / "baseline"

CONTINUOUS = [
    "sqft",
    "bedrooms",
    "bathrooms",
    "year_built",
    # walk_score_resid = walk_score - archetype_mean(walk_score):
    # orthogonal to archetype dummies, captures within-archetype walkability deviation only.
    "walk_score_resid",
    "median_income",
]

STYLE_REF_COL = "style_Baseline_EmptyRoom"

# Expected signs on *raw-scale* continuous terms after positive scaling (same sign as coefficient in model)
SIGN_SANITY: dict[str, dict[str, str | None]] = {
    "log_psf": {
        "sqft": "-",
        "year_built": "+",
    },
    "log_dom": {
        "year_built": None,
    },
}


def _arch_style_dummies(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[str], list[str]]:
    arch = pd.get_dummies(df["dominant_archetype"], prefix="arch", drop_first=True)
    sty = pd.get_dummies(df["style_g"], prefix="style")
    if STYLE_REF_COL in sty.columns:
        sty = sty.drop(columns=[STYLE_REF_COL])
    arch_cols = list(arch.columns)
    sty_cols = list(sty.columns)
    return arch, sty, arch_cols, sty_cols


def _align_dummies(
    df: pd.DataFrame,
    arch_cols: list[str],
    sty_cols: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    arch = pd.get_dummies(df["dominant_archetype"], prefix="arch", drop_first=True)
    sty = pd.get_dummies(df["style_g"], prefix="style")
    if STYLE_REF_COL in sty.columns:
        sty = sty.drop(columns=[STYLE_REF_COL])
    for c in arch_cols:
        if c not in arch.columns:
            arch[c] = 0
    arch = arch[arch_cols].astype(float)
    for c in sty_cols:
        if c not in sty.columns:
            sty[c] = 0
    sty = sty[sty_cols].astype(float)
    return arch.values, sty.values


def build_matrix(
    df: pd.DataFrame,
    arch_cols: list[str],
    sty_cols: list[str],
    scaler: StandardScaler | None,
    fit_scaler: bool,
) -> tuple[np.ndarray, StandardScaler]:
    Xc = df[CONTINUOUS].astype(float).values
    if scaler is None:
        scaler = StandardScaler()
    if fit_scaler:
        Xc = scaler.fit_transform(Xc)
    else:
        Xc = scaler.transform(Xc)
    arch_m, sty_m = _align_dummies(df, arch_cols, sty_cols)
    X = np.hstack([Xc, arch_m, sty_m])
    return X, scaler


def feature_names(arch_cols: list[str], sty_cols: list[str]) -> list[str]:
    return CONTINUOUS + arch_cols + sty_cols


def _mape_logspace(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    yt = np.exp(y_true)
    yp = np.exp(y_pred)
    return float(np.mean(np.abs((yt - yp) / np.clip(yt, 1e-9, None))) * 100.0)


def loo_eval_folds(
    sub: pd.DataFrame,
    arch_cols: list[str],
    sty_cols: list[str],
    y: np.ndarray,
    factory: Any,
) -> dict[str, float]:
    loo = LeaveOneOut()
    preds = np.zeros_like(y, dtype=float)
    for train_idx, test_idx in loo.split(sub):
        tr = sub.iloc[train_idx]
        te = sub.iloc[test_idx]
        X_tr, scaler = build_matrix(tr, arch_cols, sty_cols, None, True)
        X_te, _ = build_matrix(te, arch_cols, sty_cols, scaler, False)
        m = factory()
        m.fit(X_tr, y[train_idx])
        preds[test_idx] = m.predict(X_te)
    return {
        "rmse": float(np.sqrt(mean_squared_error(y, preds))),
        "mae": float(mean_absolute_error(y, preds)),
        "mape_pct": _mape_logspace(y, preds),
    }


def coef_table(model: Any, names: list[str], intercept: float) -> list[dict[str, Any]]:
    rows = [{"feature": "intercept", "coef": float(intercept)}]
    coef = np.ravel(model.coef_)
    for n, c in zip(names, coef):
        rows.append({"feature": n, "coef": float(c)})
    return rows


def sign_sanity_report(target: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expect = SIGN_SANITY.get(target, {})
    by_name = {r["feature"]: r["coef"] for r in rows}
    out = []
    for feat, want in expect.items():
        if want is None or feat not in by_name:
            continue
        c = by_name[feat]
        ok = (want == "+" and c > 0) or (want == "-" and c < 0) or (want == "0" and abs(c) < 1e-12)
        out.append({"feature": feat, "expected": want, "coef": c, "pass": bool(ok)})
    return out


def style_lift_ranking(rows: list[dict[str, Any]], target: str) -> list[dict[str, Any]]:
    styles = [r for r in rows if r["feature"].startswith("style_")]
    if target == "log_psf":
        styles.sort(key=lambda r: r["coef"], reverse=True)
    else:
        styles.sort(key=lambda r: r["coef"])
    return [{"rank": i + 1, **s} for i, s in enumerate(styles)]


def train_one_target(
    df_all: pd.DataFrame,
    target: str,
    eligible_col: str,
    out_dir: Path,
) -> None:
    df = df_all
    sub = df[df[eligible_col]].copy()
    if len(sub) < 10:
        raise SystemExit(f"Too few rows for {target} / {eligible_col}: {len(sub)}")
    y = sub[target].astype(float).values

    _, _, arch_cols, sty_cols = _arch_style_dummies(sub)
    names = feature_names(arch_cols, sty_cols)

    # Fixed column set from full subset
    X_full, scaler = build_matrix(sub, arch_cols, sty_cols, None, fit_scaler=True)

    factories = {
        "ols": lambda: LinearRegression(),
        "ridge": lambda: Ridge(alpha=1.0),
        "lasso": lambda: Lasso(alpha=1e-3, max_iter=20000, random_state=0),
    }

    loo_scores: dict[str, dict[str, float]] = {}
    for name, fac in factories.items():
        loo_scores[name] = loo_eval_folds(sub, arch_cols, sty_cols, y, fac)

    best_name = min(loo_scores.keys(), key=lambda k: loo_scores[k]["rmse"])

    fitted = {k: fac() for k, fac in factories.items()}
    for m in fitted.values():
        m.fit(X_full, y)

    best = fitted[best_name]
    intercept = float(best.intercept_) if hasattr(best, "intercept_") else 0.0
    tbl = coef_table(best, names, intercept)
    signs = sign_sanity_report(target, tbl)
    style_ranks = style_lift_ranking(tbl, target)

    # Archetype mean walk_score — needed at inference to compute walk_score_resid
    arch_walk_means: dict[str, float] = {}
    if "walk_score_resid" in CONTINUOUS and "walk_score" in sub.columns:
        arch_walk_means = sub.groupby("dominant_archetype")["walk_score"].mean().to_dict()

    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = {
        "target": target,
        "eligible_col": eligible_col,
        "n_samples": int(len(sub)),
        "continuous_cols": CONTINUOUS,
        "arch_columns": arch_cols,
        "style_columns": sty_cols,
        "feature_names": names,
        "scaler": scaler,
        "best_model_name": best_name,
        "loo_scores": loo_scores,
        "models": fitted,
        "archetype_walk_means": arch_walk_means,
    }
    joblib.dump(bundle, out_dir / "model.pkl")

    eval_payload = {
        "target": target,
        "eligible_col": eligible_col,
        "n_samples": len(sub),
        "reference_subsets": {
            "eligible_log_psf": int(df_all["eligible_log_psf"].sum()),
            "eligible_log_dom": int(df_all["eligible_log_dom"].sum()),
        },
        "loo": loo_scores,
        "best_model": best_name,
        "coefficients_best": tbl,
        "sign_sanity": signs,
        "style_lift_ranking": style_ranks,
        "coefficients_all": {
            name: coef_table(fitted[name], names, float(fitted[name].intercept_))
            for name in fitted
        },
    }
    (out_dir / "eval.json").write_text(json.dumps(eval_payload, indent=2), encoding="utf-8")

    md: list[str] = [
        f"# Baseline train — `{target}`",
        "",
        f"- Samples: **{len(sub)}** (`{eligible_col}`)",
        f"- Best model (LOO RMSE): **{best_name}**",
        "",
        "## LOO-CV metrics",
        "",
        "| model | RMSE | MAE | MAPE % (exp space) |",
        "|---|---:|---:|---:|",
    ]
    for name in ("ols", "ridge", "lasso"):
        s = loo_scores[name]
        md.append(f"| {name} | {s['rmse']:.6f} | {s['mae']:.6f} | {s['mape_pct']:.4f} |")
    md.extend(
        [
            "",
            "## Sign sanity (continuous)",
            "",
        ]
    )
    for row in signs:
        md.append(
            f"- `{row['feature']}`: expected {row['expected']}, coef={row['coef']:.6f} → "
            f"{'PASS' if row['pass'] else 'FAIL'}"
        )
    if not signs:
        md.append("_(no checks configured)_")
    md.extend(["", "## Style lift ranking", ""])
    for r in style_ranks:
        md.append(f"{r['rank']}. `{r['feature']}` coef={r['coef']:.6f}")
    md.extend(
        [
            "",
            "## Row counts",
            "",
            f"- eligible_log_psf: {eval_payload['reference_subsets']['eligible_log_psf']}",
            f"- eligible_log_dom: {eval_payload['reference_subsets']['eligible_log_dom']}",
            "",
            "## Coefficients (best model)",
            "",
            "| feature | coef |",
            "|---|---:|",
        ]
    )
    for r in tbl:
        md.append(f"| {r['feature']} | {r['coef']:.6f} |")
    (out_dir / "eval.md").write_text("\n".join(md), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--parquet",
        type=Path,
        help="Path to training_*.parquet (default: latest in data/derived)",
    )
    args = ap.parse_args()
    if args.parquet:
        path = args.parquet
    else:
        derived = BI_ROOT / "data" / "derived"
        cands = sorted(derived.glob("training_*.parquet"))
        if not cands:
            raise SystemExit(f"No training_*.parquet under {derived}")
        path = cands[-1]

    df = pd.read_parquet(path)
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")

    train_one_target(
        df,
        "log_psf",
        "eligible_log_psf",
        MODELS_BASE / f"log_psf_ridge_{ts}",
    )
    train_one_target(
        df,
        "log_dom",
        "eligible_log_dom",
        MODELS_BASE / f"log_dom_ridge_{ts}",
    )
    print("log_psf:", MODELS_BASE / f"log_psf_ridge_{ts}")
    print("log_dom:", MODELS_BASE / f"log_dom_ridge_{ts}")
    print("source_parquet:", path)


if __name__ == "__main__":
    main()
