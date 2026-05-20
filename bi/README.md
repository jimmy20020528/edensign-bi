# Edensign BI — Style Atlas

ZIP-level real estate staging style recommendation engine. Given a ZIP code, returns which interior design styles sell fastest and at the highest price per square foot, backed by MLS sold data and explainable ML models.

---

## What It Does

1. **Ingests** sold MLS listings (Redfin / Realtor.com scrape) with VLM-classified staging styles
2. **Trains** regularized regression models (Ridge / Lasso with LOO-CV) to isolate the causal effect of staging style on `log(price_per_sqft)` and `log(days_on_market)`
3. **Serves** a REST API that returns ranked style recommendations with confidence scores, model-predicted prices, and booster/detractor breakdowns
4. **Explains** results via GPT-4o-mini in plain English for homeowners and staging teams

---

## Architecture

```
MLS Data (Redfin / Realtor.com)
        ↓
  PostgreSQL + PostGIS          ← listing_full, listings tables
        ↓
  scripts/build_training_dataset.py   ← feature engineering + quality filters
        ↓
  data/derived/training_*.parquet
        ↓
  scripts/train_baseline_models.py    ← Ridge / Lasso / OLS + LOO-CV
        ↓
  models/baseline/log_psf_ridge_*/
  models/baseline/log_dom_ridge_*/
        ↓
  FastAPI (app/main.py)               ← /analyze/by-zipcode
        ↓
  React Frontend (/ui/)               ← Style Atlas Dashboard
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| API framework | FastAPI + uvicorn |
| Database | PostgreSQL 16 + PostGIS 3.4 |
| ML models | scikit-learn (Ridge, Lasso, OLS) |
| Data processing | pandas, numpy, pyarrow |
| Model serialization | joblib |
| VLM style classification | Gemini 2.5 Pro (→ migrating to Qwen3.6-35B-A3B) |
| LLM explanation | OpenAI GPT-4o-mini |
| Frontend | React 18 (CDN, no build step) + Babel standalone |
| Local DB | Docker + postgis/postgis:16-3.4 |

---

## Quick Start

### 1. Prerequisites

- Python 3.12
- Docker Desktop
- `git clone` this repo

### 2. Environment

```bash
cp .env.example .env
# Fill in your API keys in .env
```

### 3. Start the database

```bash
docker compose up -d
# Starts PostgreSQL on localhost:5432
# Automatically applies schema.sql on first run
```

### 4. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 5. Populate data

```bash
# Pull sold listings (Redfin / Realtor)
python scripts/redfin_scrape.py
python scripts/realtor_pull.py

# Fetch walk scores and location features
python scripts/fetch_location_scores.py --min-sold-date 2025-05-01

# Classify staging styles with VLM
python scripts/classify_styles.py
```

### 6. Train models

```bash
# Build training parquet (with quality filters)
python scripts/build_training_dataset.py --min-sold-date 2025-05-01

# Train Ridge / Lasso / OLS with LOO-CV
python scripts/train_baseline_models.py
```

### 7. Start the API

```bash
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000/ui/` in your browser.

---

## API Endpoints

### `GET /analyze/by-zipcode`

Returns ranked staging style recommendations for a ZIP code.

```
GET /analyze/by-zipcode?zipcode=02135&objective=balanced&scoring_mode=hybrid
```

**Parameters:**

| Name | Values | Default | Description |
|---|---|---|---|
| `zipcode` | 5-digit US ZIP | required | Target market |
| `objective` | `balanced` `fast` `price` | `balanced` | Optimization goal |
| `scoring_mode` | `heuristic` `model` `hybrid` | `heuristic` | Scoring method |

**Scoring modes:**
- `heuristic` — weighted median DOM + price from historical data
- `model` — Ridge/Lasso predicted log(psf) and log(dom)
- `hybrid` — blend of both (recommended)

### `POST /analyze/explain/by-zipcode`

Same analysis + GPT-4o-mini narrative explanation.

```json
{
  "zipcode": "02135",
  "objective": "balanced",
  "scoring_mode": "hybrid",
  "client_context": { "language": "English", "audience": "homeowner_or_staging_team" }
}
```

### `GET /health`

```json
{ "status": "ok" }
```

Full interactive docs at `http://localhost:8000/docs`.

---

## Training Pipeline

### Model targets

| Target | Description | Current LOO-CV MAPE |
|---|---|---|
| `log_psf` | log(price per sqft) | ~24% |
| `log_dom` | log(days on market) | ~57% |

### Features

**Continuous** (StandardScaler normalized):

| Feature | Description |
|---|---|
| `sqft` | Property size |
| `bedrooms` | Bedroom count |
| `bathrooms` | Bathroom count |
| `year_built` | Construction year |
| `walk_score_resid` | Walk score minus archetype mean (within-group deviation) |
| `median_income` | ZIP median household income |
| `months_since_2022_q1` | Linear time trend |
| `months_since_2022_q1_sq` | Quadratic time trend |

**Categorical** (one-hot encoded):
- `dominant_archetype` — buyer demographic archetype (young_professional, student_budget, mixed)
- `style_g` — staging style, reference = `Baseline_EmptyRoom`

### Quality filters

Rows excluded from training:
- `sqft > 3500` — multifamily rental buildings
- `bedrooms > 8` — multifamily buildings
- `price_per_sqft < $250` — impossible for Boston residential

### Best model selection

LOO-CV RMSE selects the best among OLS / Ridge / Lasso per target. Currently Lasso wins for both targets (sparse feature selection).

---

## Project Structure

```
bi/
├── app/
│   ├── main.py                    # FastAPI app, routes, static files
│   └── services/
│       ├── zipcode_analyzer.py    # Core recommendation engine
│       └── gpt_explainer.py       # OpenAI narrative generation
├── scripts/
│   ├── build_training_dataset.py  # Task 1: feature engineering → parquet
│   ├── train_baseline_models.py   # Task 2: Ridge/Lasso/OLS + LOO-CV
│   ├── fetch_location_scores.py   # Walk Score + transit score fetch
│   ├── classify_styles.py         # VLM style classification (Gemini)
│   ├── redfin_scrape.py           # Redfin sold listings scraper
│   ├── realtor_pull.py            # Realtor.com listings via homeharvest
│   ├── census_pull.py             # US Census ACS income data
│   ├── migrations/                # SQL schema migrations
│   └── db_dsn.py                  # PostgreSQL connection helper
├── frontend/
│   └── index.html                 # Style Atlas Dashboard (self-contained React app)
├── config/
│   ├── style_taxonomy.json        # 20 staging style definitions
│   └── acs_variables.json         # Census variable codes
├── schema.sql                     # Database schema
├── docker-compose.yml             # Local PostgreSQL + PostGIS
├── requirements.txt
└── .env.example                   # Environment variable template
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Description |
|---|---|---|
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | Yes | PostgreSQL connection |
| `OPENAI_API_KEY` | Yes | GPT-4o-mini for explanations |
| `GEMINI_API_KEY` | Yes | VLM style classification |
| `WALKSCORE_API_KEY` | Yes | Walk Score API |
| `RENTCAST_API_KEY` | No | Rental market data |
| `FRED_API_KEY` | No | Federal Reserve economic data |
| `CENSUS_API_KEY` | No | US Census ACS (works without key at lower rate limit) |
| `RUNPOD_API_KEY` | No | For self-hosted VLM inference |

---

## Roadmap

- [ ] Expand beyond Allston (02135) to multi-city dataset
- [ ] Switch VLM from Gemini to Qwen3.6-35B-A3B FP8 (self-hosted on RunPod)
- [ ] Add S3 photo storage for reproducible VLM re-classification
- [ ] Migrate PostgreSQL from localhost to AWS RDS
- [ ] Add school ratings, crime index, MBTA proximity as features
- [ ] XGBoost + SHAP when dataset reaches ~5,000 listings
- [ ] Accept listing-level inputs (sqft, bedrooms) instead of ZIP median

---

## Data Notes

- Training data covers **sold listings only** (not active)
- Style labels are assigned by VLM (Gemini 2.5 Pro) from listing photos
- `EmptyRoom` style serves as the regression baseline — all other style coefficients represent premium/discount relative to an empty/unstaged property
- DOM model has high error (~57% MAPE) due to confounding from listing price strategy; use with caution

---

## License

Internal — Edensign © 2026
