# Edensign — CLAUDE.md

LLM-facing guide. Read this when working in any subproject — explains how the pieces fit and what NOT to break.

## Product

Edensign is a pre-market real-estate platform. A seller uploads photos + a property location → the system produces a pre-listing package: market analysis for the ZIP, per-room condition + quality assessment, recommended staging style, and a draft listing description. The entry point is the Listing Wizard (`bi/frontend/wizard.html`).

## Active services

```
bi                FastAPI, port 8000
                  - ZIP-level market analysis (style, HMDA buyers, Walk Score,
                    schools, FRED macro, Redfin market)
                  - Address → ZIP geocoding via Google Geocoder
                  - Postgres + PostGIS
                  - Listing description writer (/listing/write)
                  - Frontend in bi/frontend/ (Style Atlas + Listing Wizard)

home-report-ai    FastAPI, port 8001
                  - 30-photo property → Q/C ratings + per-room rationale +
                    must_do/recommended/optional suggestions
                  - VLM (Gemini/OpenAI) for Stage 1 perception and Stage 5 polish
                  - Stages 2-4 are pure Python (rules + scoring), no LLMs

agent             FastAPI, port 8002
                  - Wraps bi + home-report-ai for the wizard
                  - /pipeline/run is the one-shot endpoint the wizard calls
                  - Also exposes per-tool endpoints for Langflow agents

cv-models         Standalone module — NOT in the live pipeline
                  - Three DINOv2 + linear probe classifiers (occupancy, furnished
                    room type, empty room type)
                  - Trained but artifacts are .gitignored — regenerate via scripts
                  - cv-models/serve_roma/ is a placeholder for RunPod GPU
                    (RoMa indoor matcher for instance grouping). Not built yet.
```

## Pipeline today (what actually runs)

```
Browser (wizard.html)
  │  multipart: photos + zipcode_or_address
  ▼
agent /pipeline/run (port 8002)
  │
  ├─→ bi /analyze/by-zipcode         recommended style + market context
  ├─→ bi /analyze/explain/...        LLM-written summary + buyer profile
  ├─→ home-report-ai /report         per-room Q/C + suggestions (VLM-based)
  └─→ bi /listing/write              2-3 paragraph listing description
  │
  ▼
returns: { zipcode, address, n_photos, home_report, bi_analysis,
           bi_explain, listing_text }
```

cv-models is NOT in this pipeline today. home-report-ai still uses VLM for Stage 1 room classification. When/if cv-models is integrated, home-report-ai will accept a `room_type` hint and skip its own Stage 1.

## DO NOT BREAK

1. **Ports are contractual.** 8000=bi, 8001=home-report-ai, 8002=agent, 5173=frontend. The agent reads URLs from env (`BI_BASE`, `HOME_REPORT_BASE`); changing the port without updating env breaks `/pipeline/run`.

2. **`bi/.env` is the source of truth for shared API keys** (OPENAI_API_KEY, GEMINI_API_KEY, WALKSCORE_API_KEY, GOOGLE_MAPS_API_KEY, GREATSCHOOLS_API_KEY, CENSUS_API_KEY, RENTCAST_API_KEY, FRED_API_KEY, DB_*). Other services source it. Don't duplicate keys per-service. Don't commit `.env` — use `.env.example`.

3. **`/pipeline/run` response schema is consumed by `bi/frontend/wizard.html`.** Top-level keys: `zipcode`, `address`, `n_photos`, `home_report`, `bi_analysis`, `bi_explain`, `listing_text`. Changing keys requires a frontend update in the same commit. The frontend extracts `bi_explain.llm` before passing to `mapAnalysis()`.

4. **`bi/frontend/index.html` and `bi/frontend/wizard.html` share components by copy, not import.** Babel-in-browser doesn't support modules. When updating a shared Style Atlas display component, edit it via `bi/frontend/build_wizard.py` which composes wizard.html from index.html. Editing wizard.html directly will be overwritten on the next build.

5. **VLM cost is real.** Each VLM call to OpenAI/Anthropic/Gemini costs money and adds 5-30s latency. Don't add new VLM calls without explicit reason; default to rule-based or cv-models-based alternatives.

6. **home-report-ai Stages 2, 3, 4 call NO LLMs.** Pure Python only. Every suggestion comes from `src/knowledge/suggestions.yaml`. VLM only describes; rules make judgments; LLM only polishes (Stage 5).

7. **cv-models writes only to its own `artifacts/`.** Don't import or write into other modules' directories. Consumers load saved files (`classifier.pkl`, `class_names.json`) as opaque artifacts.

8. **Don't break the existing demo.** `bi/frontend/index.html` (Style Atlas) is the production interface. The wizard adds capabilities on top of it; doesn't replace it. Keep Style Atlas working.

## How to make changes

- Touch one service at a time. Cross-service changes (frontend + agent + home-report) should land as a single coordinated commit with all touched services updated.
- When adding a new endpoint, update the consumer in the same change.
- Don't introduce new dependencies without checking the venv is right (each service has its own `.venv`).
- See each subproject's own `README.md` for module-specific rules.

## Status snapshot

| Module                                                  | Status                    |
|---------------------------------------------------------|---------------------------|
| bi market analysis API + Style Atlas frontend           | Production                |
| home-report-ai 5-stage pipeline                         | Production                |
| agent tool service + Langflow component                 | Production                |
| `/pipeline/run` one-shot endpoint                       | Production                |
| Listing Wizard (wizard.html) end-to-end                 | Production                |
| Address → ZIP geocoding in wizard                       | Production                |
| cv-models classifiers (occupancy/furnished/empty)       | Trained, NOT integrated   |
| cv-models RoMa instance grouping                        | Not built (RunPod target) |
| home-report-ai integration (skip Stage 1 + use hints)   | Not started               |
| Frontend per-instance card display                      | Not started               |
