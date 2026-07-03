#!/usr/bin/env python3
"""
Edensign BI — scrape.py
=======================
One-stop CLI that runs the full data pipeline for a city or set of ZIPs:
  1. Redfin + Realtor.com scrape
  2. Location scores (Walk Score / transit)
  3. Data quality cleaning
  4. VLM style classification (optional — costs ~$0.025/listing)
  5. Model retrain (optional)

Usage:
    python scripts/scrape.py --city Boston --state MA
    python scripts/scrape.py --city Chicago --state IL --skip-classify
    python scripts/scrape.py --zip 02135 02134
    python scripts/scrape.py --city Boston --state MA --retrain
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BI_ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], label: str) -> bool:
    """Run a command, stream output, return True on success."""
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    result = subprocess.run(cmd, cwd=BI_ROOT)
    if result.returncode != 0:
        print(f"\n  ✗ {label} failed (exit {result.returncode})")
        return False
    print(f"\n  ✓ {label} done")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Edensign BI full scrape pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    zip_group = ap.add_mutually_exclusive_group(required=True)
    zip_group.add_argument("--city", metavar="CITY", help="City name (e.g. Boston)")
    zip_group.add_argument("--zip", nargs="+", metavar="ZIPCODE", help="One or more ZIP codes")
    ap.add_argument("--state", metavar="STATE", help="Two-letter state code, required with --city")
    ap.add_argument("--skip-classify", action="store_true", help="Skip VLM style classification (saves cost)")
    ap.add_argument("--retrain", action="store_true", help="Rebuild training dataset and retrain models after scrape")
    ap.add_argument("--past-days", type=int, default=1095, help="How many days back to pull sold listings (default 1095 = 3yr)")
    args = ap.parse_args()

    if args.city and not args.state:
        ap.error("--state is required when using --city")

    py = sys.executable

    # Build the ZIP/city args that get forwarded to each scraper
    if args.city:
        location_args = ["--city", args.city, "--state", args.state]
    else:
        location_args = ["--zip"] + args.zip

    steps: list[tuple[str, list[str]]] = [
        (
            "Redfin scrape",
            [py, "scripts/ingestion/redfin_scrape.py"] + location_args,
        ),
        (
            "Realtor.com scrape",
            [py, "scripts/ingestion/realtor_pull.py"] + location_args + ["--past-days", str(args.past_days)],
        ),
        (
            "Census ACS (median_income + dominant_archetype)",
            [py, "scripts/ingestion/census_pull.py"],
        ),
        (
            "Location scores (walk_score / transit_score / amenity_count_1km)",
            [py, "scripts/enrichment/fetch_location_scores.py"],
        ),
        (
            "Data quality cleaning",
            [py, "scripts/cleaning/clean_outliers.py"],
        ),
    ]

    if not args.skip_classify:
        steps.append((
            "VLM style classification (Gemini)",
            [py, "scripts/enrichment/classify_styles.py"],
        ))

    if args.retrain:
        steps += [
            (
                "Build training dataset",
                [py, "scripts/training/build_training_dataset.py"],
            ),
            (
                "Train models (Ridge/Lasso/OLS + LOO-CV)",
                [py, "scripts/training/train_baseline_models.py"],
            ),
        ]

    print(f"\nEdensign BI — Scrape Pipeline")
    if args.city:
        print(f"Target: {args.city}, {args.state}")
    else:
        print(f"Target ZIPs: {args.zip}")
    print(f"Steps: {len(steps)}  |  classify={'yes' if not args.skip_classify else 'skipped'}  |  retrain={'yes' if args.retrain else 'skipped'}")

    failed = []
    for label, cmd in steps:
        ok = run(cmd, label)
        if not ok:
            failed.append(label)

    print(f"\n{'=' * 60}")
    if failed:
        print(f"  Pipeline finished with {len(failed)} failure(s):")
        for f in failed:
            print(f"    ✗ {f}")
        sys.exit(1)
    else:
        print(f"  ✓ All {len(steps)} steps completed successfully")


if __name__ == "__main__":
    main()
