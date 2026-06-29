# Edensign Decision BI Plan

## 1) Vision

Build a data-driven Decision BI system that recommends the best staging style for a target ZIP code (and later for a specific property profile), optimizing for:

- Faster sale (`days_on_market`)
- Better pricing (`price_per_sqft`, later `sale_to_list_ratio`)
- Clear confidence and explainability for business users

This is not a static dashboard BI. It is a recommendation-oriented BI service.

---

## 2) Business Objective

### Primary Objective

Given a ZIP code, return ranked staging styles that are most likely to:

- Sell faster
- Sell higher

### Secondary Objective

Provide confidence and evidence so users can trust the recommendation:

- Sample support (`n_listings`)
- Confidence score
- Key drivers and comparable examples (next phases)

---

## 3) Current State (Updated 2026-05-01)

### Phase 1 Status: ✅ Complete (v2 in production)

Stage A baseline modeling pipeline shipped. v2 model trained on past-1-year subset
(George's directive); v1 retained as fallback.

### Implemented capabilities

- **Data layer**: 2518 Redfin listings (5-year sold + active for 02134/02135),
  611 with photos + style classifications. Schema also supports Realtor / Zillow /
  RentCast cross-source data via `source` + `canonical_id` columns (HomeHarvest
  realtor integration coming in Phase 2 expansion).
- **Feature layer**: `listing_full` view joins 5 base tables; 14 features go into model
  (Property + Location + Demographics + Time + Style). See [FACTORS.md](FACTORS.md).
- **VLM classification**: Gemini 2.5 Pro with 23-class taxonomy (20 pro-staged styles +
  EmptyRoom + Lived-in + Unclassified). Stratified photo sampling, 503 retry.
- **Cleaning gate**: 5 data_quality_flag values (rental_leakage / list_eq_sold /
  active_only / cross_period / no_interior_photos), informational vs hard-exclude
  distinction.
- **Stage A model**: Lasso/Ridge/OLS dual targets (log_psf + log_dom), LOO-CV.
  Production: log_psf MAPE 13.15%, log_dom MAPE 63.75%.
- **API**: `/analyze/by-zipcode` with `scoring_mode=heuristic|model|hybrid`, evidence
  block (top drivers), warnings framework (4 types), confidence degradation.

### Documents

- [PROGRESS.md](PROGRESS.md) — append-only progress log
- [FACTORS.md](FACTORS.md) — factor inventory
- [ISSUES.md](ISSUES.md) — 10 known issues + workarounds
- [RUNBOOK.md](RUNBOOK.md) — operational commands
- [PRESENTATION.md](PRESENTATION.md) — speaking script for stakeholder demo

### Current MVP scoring formula (heuristic mode, default)

`score = 0.45 * speed + 0.45 * price + 0.10 * support`

### Known data limitations

- `list_price` quality issue (many rows where `list_price ~= sold_price`)
- Small sample size in some ZIPs
- Incomplete factors for school/crime/flood in some rows

---

## 4) Target Architecture

## 4.1 Logical Layers

1. **Data Layer**
   - Ingestion scripts (Redfin/Census/Location/Market/Style)
   - PostgreSQL + PostGIS storage
2. **Feature Layer**
   - `listing_full` + engineered features
   - Time-window features (recent 90/180/365 days)
3. **Decision/Model Layer**
   - Baseline models for DOM and PPSF
   - Style lift / counterfactual scoring
   - Confidence and reliability gating
4. **Serving Layer**
   - FastAPI endpoints
   - JSON responses for app/dashboard consumption
5. **Ops Layer**
   - Batch refresh jobs
   - Backtest + drift monitoring
   - Model/data versioning

## 4.2 Recommended Modeling Pattern

Use a two-stage approach:

- **Stage A: Baseline performance model** (without style)
  - Predict expected DOM and PPSF from Property + Location + Market + Demographics
- **Stage B: Style lift model**
  - Estimate incremental effect from style and style-context interaction
- **Decision layer**
  - Run counterfactual scoring for candidate styles (change style, keep others fixed)
  - Return ranked recommendation with confidence

---

## 5) Factor Strategy

Do not hardcode independent weights for all 53 factors in one flat formula.

### 5.1 Factor Groups

- Property
- Location
- Market
- Demographics
- Staging/Visual

### 5.2 Weighting Principle

- Model learns feature-level weights/patterns
- Business objective controls outcome-level weights:
  - `objective=fast`: DOM weighted higher
  - `objective=price`: PPSF weighted higher
  - `objective=balanced`: equal-ish mix
- Confidence gate downweights recommendations when support is weak

---

## 6) Product API Roadmap

### Phase 1 (Current MVP)

- `GET /analyze/by-zipcode?zipcode=02135`
- Output:
  - `recommended_styles` (Top 3)
  - per-style score breakdown
  - confidence summary

### Phase 2 (Near-term)

- Add objective parameter:
  - `objective=fast|price|balanced`
- Add optional property profile:
  - `property_type`, `sqft`, `beds`, `baths`, `year_built`
- Add evidence block:
  - supporting comps and key factors

### Phase 3 (Industrial)

- Model-backed recommendations (DOM + PPSF models)
- Counterfactual simulation endpoint
- Explainability endpoint (top drivers / SHAP-like summary)

---

## 7) KPI and Evaluation

## 7.1 Offline metrics

- DOM model: MAE/RMSE on log DOM
- PPSF model: MAPE/RMSE
- Ranking quality: top-1 and top-3 hit rate in historical replay

## 7.2 Reliability metrics

- Coverage by ZIP (how many ZIPs have enough data)
- Median support per recommendation
- Confidence calibration (predicted confidence vs observed error)

## 7.3 Business metrics (later)

- Reduction in median DOM vs baseline strategy
- Improvement in sale price metrics vs baseline style choice

---

## 8) Governance and Risk Controls

- Explicitly label recommendation as data-driven guidance, not appraisal
- Avoid individual-level inference from demographic aggregates
- Report uncertainty for low-data areas
- Keep human override capability in the product

---

## 9) 30/60/90 Day Execution Plan

## Day 0-30 (Stabilize MVP)

- Keep ZIP recommendation endpoint stable
- Fix data quality blockers (`list_price`, `listed_date`)
- Add objective switch (fast/price/balanced)
- Add minimum support thresholds and low-confidence handling

## Day 31-60 (Model Upgrade)

- Build baseline DOM/PPSF training pipeline
- Implement rolling backtest
- Integrate model outputs into API response (alongside MVP score)

## Day 61-90 (Industrialize)

- Add style counterfactual scoring
- Add explainability summary
- Add scheduled retrain + monitoring dashboards

---

## 10) Definition of Done (Industrial Readiness)

The Decision BI system is considered industrial-ready when:

- Data refresh is automated and monitored
- Model performance is backtested and tracked over time
- API returns recommendation + confidence + evidence
- Low-quality data scenarios are handled gracefully
- Product documentation and interpretation guidelines are published

---

## 11) Immediate Next Steps

1. Add `objective` mode to `/analyze/by-zipcode`
2. Add optional property-profile filters to improve personalization
3. Implement support threshold policy and confidence gates
4. Start baseline model training scripts with rolling backtest

