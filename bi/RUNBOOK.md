# Edensign BI — Runbook

> How to run the full pipeline from scratch and do regular maintenance.
> Working directory: the `bi/` folder of the edensign-repo monorepo.

---

## 0. One-time environment setup

### 0.1 Python venv — requires Python 3.12+

```bash
# macOS: brew install python@3.12
# Linux: apt-get install python3.12 python3.12-venv

cd bi
python3.12 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt

python --version   # expect Python 3.12.x
```

### 0.2 .env file

`bi/.env` must contain:

```
DB_PASSWORD=edensign_dev
GEMINI_API_KEY=<from https://aistudio.google.com/app/apikey>
WALKSCORE_API_KEY=<optional, used by fetch_location_scores.py>
```

### 0.3 Start Docker Postgres

```bash
docker compose up -d
docker ps --filter name=edensign_bi_db   # should show (healthy)
```

### 0.4 Apply schema

First time (empty database):
```bash
docker exec -i edensign_bi_db psql -U edensign -d edensign_bi < schema.sql
```

Apply migrations on an existing database:
```bash
for f in scripts/migrations/*.sql; do
  echo ">>> $f"
  docker exec -i edensign_bi_db psql -U edensign -d edensign_bi < "$f"
done
```

---

## 1. Full pipeline — one command

```bash
source .venv/bin/activate
python scripts/scrape.py --city Boston --state MA --retrain
```

This runs in order:
1. `scripts/ingestion/scraper.py` — Redfin + Realtor.com, deduped by mls_id
2. `scripts/ingestion/census_pull.py` — median_income + dominant_archetype
3. `scripts/enrichment/fetch_location_scores.py` — walk_score / transit / amenity
4. `scripts/cleaning/clean_outliers.py` — data quality flags
5. `scripts/enrichment/classify_styles.py` — Gemini VLM style classification
6. `scripts/training/build_training_dataset.py` — build training parquet
7. `scripts/training/train_baseline_models.py` — Ridge/Lasso/OLS + LOO-CV

**Scrape window**: automatically Jan 1 of previous calendar year → today.  
**Training window**: automatically rolling 365 days from today.

Skip VLM classification (saves ~$13):
```bash
python scripts/scrape.py --city Boston --state MA --retrain --skip-classify
```

Target specific ZIPs instead of a full city:
```bash
python scripts/scrape.py --zip 02135 02134 --retrain
```

---

## 2. Running individual steps

### 2.1 Scrape listings (Redfin + Realtor combined)

```bash
python scripts/ingestion/scraper.py --city Boston --state MA
python scripts/ingestion/scraper.py --zip 02135 02134
python scripts/ingestion/scraper.py --city Boston --state MA --type all   # sold + for_sale + rent
```

Default type is `sold`. Deduplicates across Redfin and Realtor by mls_id.
Time window defaults to Jan 1 of previous year — override with `--past-days 180`.

### 2.2 Census ACS (run once, or when expanding to new cities)

```bash
python scripts/ingestion/census_pull.py
```

⚠️ Currently hardcoded to Suffolk County, MA (FIPS 25/025). Must be edited for other cities.

### 2.3 Location scores

```bash
python scripts/enrichment/fetch_location_scores.py
```

Requires `WALKSCORE_API_KEY` in `.env`. Fills walk_score, transit_score, amenity_count_1km for any listing that's missing them.

### 2.4 Data quality cleaning

```bash
python scripts/cleaning/clean_outliers.py
```

Sets `data_quality_flag` on listings: rental_leakage, cross_period, no_interior_photos, bad_sqft, etc.

### 2.5 VLM style classification

```bash
python scripts/enrichment/classify_styles.py
```

Skips already-classified listings automatically. Each listing ~$0.025, ~10s. ~100 listings ≈ $2.50 + 30 min.

### 2.6 Build training dataset

```bash
python scripts/training/build_training_dataset.py
```

Reads from DB, applies quality filters, writes `data/derived/training_<timestamp>.parquet`.
Training cutoff = rolling 365 days from today.

### 2.7 Train models

```bash
python scripts/training/train_baseline_models.py
```

Reads latest parquet from `data/derived/`. Writes model artifacts to `models/baseline/log_{psf,dom}_ridge_<timestamp>/`.

---

## 3. Start the API

```bash
source .venv/bin/activate
uvicorn app.main:app --port 8000
```

- Dashboard: http://localhost:8000/ui/
- Swagger: http://localhost:8000/docs
- Health: http://localhost:8000/health

```bash
# Quick test
curl -s 'http://localhost:8000/analyze/by-zipcode?zipcode=02135&objective=balanced&scoring_mode=hybrid' | python3 -m json.tool
```

---

## 4. Weekly refresh (~10 minutes)

```bash
source .venv/bin/activate

# Pull new listings + reclassify + retrain
python scripts/scrape.py --city Boston --state MA --retrain

# Restart API to load new model
# kill the uvicorn process, then:
uvicorn app.main:app --port 8000
```

---

## 5. Cold start (empty DB → demo-ready, ~1.5 hours)

```bash
# 0. Start DB and apply schema
docker compose up -d
docker exec -i edensign_bi_db psql -U edensign -d edensign_bi < schema.sql
for f in scripts/migrations/*.sql; do
  docker exec -i edensign_bi_db psql -U edensign -d edensign_bi < "$f"
done
source .venv/bin/activate

# 1. Run full pipeline
python scripts/scrape.py --city Boston --state MA --retrain

# 2. Start API
uvicorn app.main:app --port 8000
```

---

## 6. Useful diagnostic SQL

Run via: `docker exec edensign_bi_db psql -U edensign -d edensign_bi -c "SQL"`

### Row counts by source
```sql
SELECT source, COUNT(*) total,
  COUNT(*) FILTER (WHERE sold_date IS NOT NULL) sold,
  COUNT(*) FILTER (WHERE sold_date >= CURRENT_DATE - 365) sold_past_year
FROM listings GROUP BY source;
```

### Style distribution
```sql
SELECT primary_style, COUNT(*)
FROM style_classifications
GROUP BY 1 ORDER BY 2 DESC;
```

### Listings missing classification
```sql
SELECT COUNT(*) FROM listings l
WHERE NOT EXISTS (
  SELECT 1 FROM style_classifications sc WHERE sc.listing_id = l.listing_id
);
```

### Listings missing walk score
```sql
SELECT COUNT(*) FROM listing_full WHERE walk_score IS NULL;
```

### ZIP breakdown
```sql
SELECT zipcode, COUNT(*) FROM listings GROUP BY 1 ORDER BY 2 DESC;
```

### Cross-source dedup check (listings on both Redfin and Realtor)
```sql
SELECT COUNT(*) FROM listings WHERE canonical_id IS NOT NULL;
```

### Data quality flag breakdown
```sql
SELECT data_quality_flag, COUNT(*) FROM listings GROUP BY 1 ORDER BY 2 DESC;
```

---

## 7. Troubleshooting

### Postgres won't connect
```bash
docker ps --filter name=edensign_bi_db   # should be (healthy)
docker compose up -d
docker compose logs db | tail -50
```

### Gemini 503 / rate limit
`classify_styles.py` has exponential backoff built in (503/429 → 4s → 8s → 16s). Just re-run — already-classified listings are skipped automatically.

### Redfin returns 0 listings
- Wait 30 min and retry (temporary Cloudflare block on your IP)
- When running on RunPod this isn't an issue — datacenter IP, not home IP

### All styles classified as Unclassified
- Check `photo_urls` on the listing — may only have exterior photos
- Or photos returned 403 from Redfin CDN (re-scrape with `scraper.py`)

### Wipe DB and start over
⚠️ Destructive — only when intentional:
```bash
docker compose down -v
docker compose up -d
docker exec -i edensign_bi_db psql -U edensign -d edensign_bi < schema.sql
for f in scripts/migrations/*.sql; do
  docker exec -i edensign_bi_db psql -U edensign -d edensign_bi < "$f"
done
```

---

## 8. File index

```
bi/
├── docker-compose.yml
├── schema.sql
├── requirements.txt
├── .env / .env.example
├── PROGRESS.md
├── RUNBOOK.md
│
├── scripts/
│   ├── scrape.py                        ← one-command pipeline orchestrator
│   ├── db_dsn.py
│   ├── migrations/
│   │   ├── 001_add_emptyroom_and_livedin.sql
│   │   ├── 002_add_reasoning_column.sql
│   │   ├── 003_add_zillow_url_and_canonical_id.sql
│   │   └── 004_add_realtor_url.sql
│   ├── ingestion/
│   │   ├── scraper.py                   ← combined Redfin + Realtor scraper
│   │   ├── redfin_scrape.py             ← standalone Redfin-only
│   │   ├── realtor_pull.py              ← standalone Realtor-only
│   │   ├── census_pull.py               ← Census ACS (hardcoded Suffolk County MA)
│   │   ├── redfin_discover.py           ← legacy, not used in main pipeline
│   │   ├── redfin_detail_scrape.py      ← legacy, not used in main pipeline
│   │   ├── rentcast_pull.py             ← unused backup source
│   │   └── zillow_pull.py               ← unused (Akamai blocks)
│   ├── enrichment/
│   │   ├── classify_styles.py           ← Gemini VLM → primary_style
│   │   ├── fetch_location_scores.py     ← walk_score / transit / amenity
│   │   ├── fetch_market_snapshot.py     ← FRED mortgage rates (optional)
│   │   └── fetch_photos.py
│   ├── cleaning/
│   │   └── clean_outliers.py
│   ├── training/
│   │   ├── build_training_dataset.py
│   │   └── train_baseline_models.py
│   └── analysis/
│       ├── run_step7_analysis.py
│       └── task0_5_ols_compare.py
│
├── data/
│   └── derived/
│       └── training_<timestamp>.parquet
│
├── models/
│   └── baseline/
│       ├── log_psf_ridge_<timestamp>/
│       │   ├── model.pkl
│       │   ├── eval.json
│       │   └── eval.md
│       └── log_dom_ridge_<timestamp>/
│
└── app/
    ├── main.py
    └── services/
        └── zipcode_analyzer.py
```
