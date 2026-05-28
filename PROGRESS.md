# Edensign BI — Progress

> Last updated: 2026-05-04

## What This Is

ZIP-level real estate staging style recommendation engine. Given a ZIP code, returns which interior design styles sell fastest and at the highest price per sqft, backed by MLS sold data and Ridge/Lasso regression models.

---

## Current State (as of 2026-05-04)

### Data
- **1,022 listings** in local PostgreSQL (Docker)
- All pre-2025 data deleted — training window is **2025-01-01 to present**
- Sources: Redfin (primary) + Realtor.com (supplemental, via HomeHarvest)
- ZIPs covered: **02134, 02135** (Allston / Brighton, Boston MA)
- Style classifications: Gemini 2.5 Pro VLM (migrating to Qwen3.6-35B-A3B on RunPod)

### Model (current production)
- **Targets**: `log_psf` (price per sqft), `log_dom` (days on market)
- **Algorithm**: Ridge / Lasso / OLS + LOO-CV; best selected by LOO RMSE
- **10 input features**:
  | Feature | Source |
  |---|---|
  | sqft, bedrooms, bathrooms, year_built | Redfin / Realtor |
  | walk_score_resid, transit_score, amenity_count_1km | fetch_location_scores |
  | median_income, dominant_archetype | census_pull (Census ACS) |
  | style_g (20+ styles → EmptyRoom baseline) | classify_styles (VLM) |
- **LOO-CV performance** (latest run):
  - `log_psf`: MAPE ~24%, best model Lasso
  - `log_dom`: MAPE ~57%, best model Lasso (weak signal — DOM is noisy)
- **Artifacts**: `models/baseline/log_psf_ridge_20260504_145812/`, `log_dom_ridge_20260504_145812/`

### API
- FastAPI on `uvicorn app.main:app --port 8000`
- `GET /analyze/by-zipcode?zipcode=02135&objective=balanced&scoring_mode=hybrid`
- `scoring_mode`: `heuristic` | `model` | `hybrid` (recommended)
- Frontend dashboard served at `http://localhost:8000/ui/`
- GPT-4o-mini narrative explanation: `POST /analyze/explain/by-zipcode`

---

## Infrastructure

| Component | Current | Plan |
|---|---|---|
| Database | Local Docker PostgreSQL 16 + PostGIS | Migrate to AWS RDS |
| Photo storage | Redfin/Realtor CDN URLs (ephemeral) | S3 bucket |
| VLM classification | Gemini 2.5 Pro API | Qwen3.6-35B-A3B FP8 on RunPod |
| Training | Local Mac | Local Mac (stays) |

---

## Scripts Structure (reorganized 2026-05-04)

```
scripts/
├── scrape.py                  ← main pipeline tool (NEW)
├── db_dsn.py
├── migrations/
├── ingestion/
│   ├── redfin_scrape.py       ← --zip OR --city/--state
│   ├── realtor_pull.py        ← --zip OR --city/--state
│   ├── census_pull.py         ← hardcoded to Suffolk County MA for now
│   ├── redfin_discover.py
│   ├── redfin_detail_scrape.py
│   ├── rentcast_pull.py
│   └── zillow_pull.py
├── enrichment/
│   ├── classify_styles.py     ← currently Gemini, migrating to Qwen
│   ├── fetch_location_scores.py
│   ├── fetch_market_snapshot.py
│   └── fetch_photos.py
├── cleaning/
│   └── clean_outliers.py
├── training/
│   ├── build_training_dataset.py
│   └── train_baseline_models.py
└── analysis/
    ├── run_step7_analysis.py
    └── task0_5_ols_compare.py
```

### One-command pipeline
```bash
python scripts/scrape.py --city Boston --state MA --retrain
```
Runs: redfin_scrape → realtor_pull → census_pull → fetch_location_scores → clean_outliers → classify_styles → build_training_dataset → train_baseline_models

---

## Done

- [x] PostgreSQL schema + Docker setup
- [x] Redfin scraper (GIS API, time-window × sort-order combination to bypass 350-cap)
- [x] Realtor.com scraper (HomeHarvest, bypasses Kasada via mobile GraphQL)
- [x] Census ACS pull (median_income + dominant_archetype for Suffolk County tracts)
- [x] Walk Score / transit / amenity location scores
- [x] Gemini VLM style classification (20 styles, EmptyRoom baseline)
- [x] Data quality flags (rental_leakage, cross_period, no_interior_photos, etc.)
- [x] Quality filters: sqft 200–3500, bedrooms ≤8, psf ≥$250
- [x] Ridge/Lasso/OLS training with LOO-CV
- [x] FastAPI with heuristic / model / hybrid scoring modes
- [x] React frontend dashboard (self-contained, served at /ui/)
- [x] GPT-4o-mini narrative explanation endpoint
- [x] Scripts reorganized into ingestion / enrichment / cleaning / training / analysis subfolders
- [x] `scrape.py` one-command orchestrator (supports --city/--state or --zip, --retrain flag)
- [x] `--city / --state` args on redfin_scrape.py and realtor_pull.py (uses `zipcodes` library for ZIP lookup)
- [x] Dropped `months_since_2022_q1` time trend features (training data is 1 year, trend is meaningless)
- [x] Deleted all pre-2025 listings from DB
- [x] RUNBOOK.md corrected (wrong env var name, missing steps, deprecated script references)

---

## Next

### Immediate
- [ ] Set up RunPod with Qwen3.6-35B-A3B FP8
- [ ] Set up S3 bucket for photo storage
- [ ] Update `classify_styles.py` to call Qwen RunPod endpoint instead of Gemini
- [ ] Update `fetch_photos.py` to upload to S3

### Full-US Expansion
- [ ] Update `census_pull.py` to accept `--state-fips` / `--county-fips` args (currently hardcoded to Suffolk County MA)
- [ ] Run `scrape.py --city <city> --state <state>` for each target city
- [ ] Note: `scrape.py --city "New York" --state NY` works but NYC spans 5 counties — census_pull needs multi-county support

### Phase 2 Features (when dataset reaches ~5,000 listings)
- [ ] XGBoost + SHAP (replace Ridge/Lasso)
- [ ] School ratings (GreatSchools API)
- [ ] Crime index (local police open data)
- [ ] Migrate DB to AWS RDS

---

## Known Issues

| Issue | Impact | Status |
|---|---|---|
| `log_dom` MAPE ~57% | DOM predictions unreliable | API shows `model_dom_low_confidence` warning; use with caution |
| `census_pull.py` hardcoded to Suffolk County MA | Can't run full pipeline for other cities without editing FIPS codes | Fix before full-US expansion |
| `sale_to_list_ratio` all 0 | Feature unusable (Redfin GIS doesn't return list_price) | Excluded from model |
| Qwen VLM not set up yet | Currently using Gemini for style classification | RunPod setup pending |
| Photo URLs are CDN links (expire) | Can't re-classify styles later without re-scraping | S3 storage pending |
