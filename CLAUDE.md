# Edensign — CLAUDE.md

Top-level map of the Edensign codebase. Read this first when working in any
subproject — it explains how the pieces fit and what NOT to break across
module boundaries.

## Branches: `demo` vs `runpod-deploy`

The GitHub repo `jimmy20020528/edensign-bi` has two long-lived branches that
deploy the same product through different infrastructure.

| | `demo` | `runpod-deploy` |
|---|---|---|
| **What it is** | The stable, working demo George sees | Forward-looking RunPod serverless deployment |
| **Classify-rooms inference** | Local cv-models service on the pod (port 8003) | RunPod serverless endpoint (`edensign/cv-models` Docker image) |
| **Wizard → `/classify-rooms`** | multipart upload of raw image files | JSON `{image_urls: [...]}` after pre-uploading to `analytics.edensign.io` |
| **Photo upload step** | none — wizard just sends multipart at classify time | Frontend uploads each photo to Edensign API on selection, gets `content.edensign.io` URL |
| **External dependencies** | none beyond Edensign's own pod | RunPod serverless GPU + `analytics.edensign.io` upload API |
| **Known blocker** | none | `analytics.edensign.io` does not yet send CORS headers, so browser uploads from `*.proxy.runpod.net` fail |

**Why two branches:** `demo` is what we point George at; it must always work.
`runpod-deploy` is where the RunPod serverless migration lives so we can keep
iterating without breaking the demo. Once Haodong adds CORS support to the
Edensign upload API, `runpod-deploy` becomes the new demo and we collapse
back to one branch.

**Don't merge `runpod-deploy` into `demo`** until the CORS issue is resolved
and the full URL-based flow has been verified end-to-end in a browser.

## Product

Edensign is a pre-market real-estate platform: a seller (or listing agent)
uploads photos + a property location, and the system produces a complete
pre-listing package — market analysis for the ZIP, per-room condition and
quality assessment, recommended staging style, and a draft listing description.
The user-facing entry point today is the **Listing Wizard** (`bi/frontend/wizard.html`).

George frames the product positioning as:
- **Pre-Market Optimization Layer**
- **AI Listing Experimentation Engine**
- **Coming Soon Inventory Infrastructure**

These three are the same product viewed from different angles. Architectural
decisions should serve at least one of them.

## Active subprojects (in dependency order)

```
bi/                   FastAPI service, port 8000
                      - ZIP-level market analysis (recommended style, HMDA buyers,
                        Walk Score, schools, FRED macro, Redfin market)
                      - Address → ZIP geocoding via Google Geocoder
                      - Postgres + PostGIS, scikit-learn models, optional VLM (Gemini)
                      - React frontend in bi/frontend/ (Style Atlas + Listing Wizard)

home-report-ai/       FastAPI service, port 8001
                      - 30-photo property → Q/C ratings + per-room rationale
                        + must_do/recommended/optional suggestions
                      - Stage 1 (room type classification): VLM today, slated for
                        replacement by cv-models classifier
                      - Stage 2-4: pure Python (no LLMs), rule-driven
                      - Stage 5 (rationale polishing): VLM, stays
                      - "VLM only describes. Rules make judgments. LLM only polishes."

agent/                FastAPI service, port 8002 + Langflow custom component
                      - Tool service wrapping bi + home-report-ai for the agent
                      - /pipeline/run is the one-shot endpoint the wizard frontend calls
                      - Langflow component exposes 2 tools (analyze_zipcode,
                        generate_listing) to the chat agent

cv-models/            (NEW) standalone training + inference for CV tasks
                      - Task 1: room type classifier (DINOv2 + linear probe)
                      - Task 2: room instance grouping (DINOv2 CLS + patch matching)
                      - Replaces home-report-ai Stage 1; adds new instance grouping
                      - See cv-models/CLAUDE.md
```

Other directories (`staging/`, `serverless/`, `multiview/`, `editing datasets/`,
`bf16_merged/`, `precious_data/`) are research-stage staging-model work
(Qwen + ControlNet + LoRA). Out of scope for the current production stack.

## Pipeline (target end state, once cv-models is integrated)

```
Browser (wizard.html)
  ↓ multipart: photos + zipcode_or_address
agent/tools/server.py port 8002 /pipeline/run
  ↓
  ├─→ cv-models classifier        → per-photo room_type
  │   ↓
  │   bucket by room_type
  │   ↓
  │   cv-models instance grouping → per-bucket {room_n: [photo_idx, ...]}
  │
  ├─→ bi GET /analyze/by-zipcode  → recommended style + market context
  │   port 8000
  │
  ├─→ bi POST /analyze/explain/   → LLM-written summary + tips + buyer profile
  │     by-zipcode
  │
  ├─→ home-report-ai /report      → per instance: Q/C + per-room + suggestions
  │   port 8001                     (Stage 1 skipped, fed our room_type/groups)
  │
  └─→ OpenAI gpt-4o-mini          → 2-3 paragraph listing description grounded
                                    in the above
  ↓
returns: { zipcode, n_photos, home_report, bi_analysis, bi_explain, listing_text }
  ↓
Browser renders Style Atlas display + per-instance home report + listing
```

Today the cv-models steps are not in this pipeline — home-report-ai still does
its own VLM-based Stage 1, and there is no instance grouping. The remaining
flow already works end-to-end.

## Service map and how to restart

**Easiest: `./run.sh`** at the repo root — one command does setup (per-module
venvs + deps) and starts everything with health checks. `./run.sh {setup|start|
stop|restart|status}`; ports overridable (`HR_PORT=8011 ./run.sh start`). The
wizard is then at `http://localhost:8000/ui/wizard.html`. Full operator + deploy
guide: **`QUICKSTART.md`** (includes the nginx single-origin + systemd production
setup). The manual commands below are the equivalent of what `run.sh` does.

If services die (Mac reboot, etc.) restart in this order:

```bash
# cv-models (port 8003) — start before agent (agent proxies /classify-rooms to it)
cd cv-models && source .venv/bin/activate && uvicorn app.main:app --port 8003 &

# BI (port 8000)
cd bi && source .venv/bin/activate && uvicorn app.main:app --host '0.0.0.0' --port 8000 &

# home-report-ai (port 8001)
cd home-report-ai && source .venv/bin/activate && uvicorn src.api.main:app --port 8001 &

# Tool service (port 8002) — must load BI's .env for OPENAI_API_KEY
cd agent
set -a && source ../bi/.env && set +a
.venv/bin/python tools/server.py &

# Frontend (port 5173)
cd bi/frontend && python3 -m http.server 5173 &

# Langflow (port 7860) — Desktop App
```

Health: `curl localhost:8000/health`, `localhost:8001/health`, `localhost:8002/health`.

## Cross-module DO NOT BREAK

1. **Port numbers are contractual.** 8000=bi, 8001=home-report-ai, 8002=agent,
   8003=cv-models, 5173=frontend, 7860=Langflow. The agent tool service reads BI and home-report-ai
   URLs from env (`BI_BASE`, `HOME_REPORT_BASE`); changing the port without
   updating env breaks `/pipeline/run`.

2. **`bi/.env` is the single source of truth for API keys** (OPENAI_API_KEY,
   GEMINI_API_KEY, WALKSCORE_API_KEY, GOOGLE_MAPS_API_KEY, GREATSCHOOLS_API_KEY,
   CENSUS_API_KEY, RENTCAST_API_KEY, FRED_API_KEY, DB_*). Other services source
   it. Don't duplicate keys per-service; don't commit `.env`.

3. **`/pipeline/run` response schema is consumed by `bi/frontend/wizard.html`.**
   Top-level keys: `zipcode`, `address`, `n_photos`, `home_report`, `bi_analysis`,
   `bi_explain`, `listing_text`. Changing keys requires a frontend update in the
   same commit. `bi_explain` is the full `/analyze/explain` response — frontend
   extracts `bi_explain.llm` before passing to `mapAnalysis`.

4. **`frontend/wizard.html` is hand-maintained — edit it directly.** It and
   `frontend/index.html` share components by copy, not import (Babel-in-browser
   doesn't support modules). There is no build step: `build_wizard.py` was removed
   because it had drifted far behind the hand-edited `wizard.html` and regenerating
   from it wiped real features. Edit `wizard.html` directly; never regenerate it.

5. **`home-report-ai` is allowed to keep VLM calls in Stage 1 and Stage 5
   until cv-models integration ships.** Don't strip VLM from home-report-ai
   prematurely — it's the only fallback right now.

6. **cv-models writes only to its own `artifacts/`.** Don't import or write
   into other modules' directories. Consumers load the saved files
   (`classifier.pkl`, `class_names.json`) as opaque artifacts.

7. **VLM cost is real.** Each VLM call to OpenAI/Anthropic/Gemini costs money
   and adds 5-30s latency. Don't add new VLM calls without explicit reason;
   default to rule-based or cv-models-based alternatives.

8. **Don't break the existing demo.** `bi/frontend/index.html` (Style Atlas)
   is the production interface George has seen. The wizard adds capabilities
   on top of it; it doesn't replace it yet. Keep Style Atlas working.

## Roadmap (what's done / what's next)

| Module                                                | Status                |
|-------------------------------------------------------|-----------------------|
| bi market analysis API + Style Atlas frontend         | Done, production      |
| home-report-ai Stage 1-5 pipeline                     | Done, port 8001 up    |
| agent tool service + Langflow agent                   | Done, chat works      |
| `/pipeline/run` one-shot endpoint                     | Done                  |
| Listing Wizard (wizard.html) full UI                  | Done, end-to-end runs |
| Address → ZIP geocoding in wizard                     | Done                  |
| cv-models classifier + Task 2 instance grouping        | **Done, wired into `/classify-rooms`** (starter model; real ~2,600-photo retrain in progress) |
| Photo walk-through ordering (cv-models `/walkthrough`) | **Done** — group-aware, public-before-private, drag-reorder + download in wizard |
| Room editor: drag/group/**delete photos** (syncs to upload) | **Done** |
| Async background room classification (wizard)          | **Done** |
| Neighborhood analysis (`/analyze/neighborhood`, OSM)   | **Done** |
| Comparable Sales / CMA (`/analyze/comps`, Redfin)      | **Done** — type/size/year/distance filtering, "Your home" row, best-match highlight |
| Buyer Appeal (`/analyze/buyer-appeal`) + Market Positioning | **Done** (grounded) |
| Q/C shown as 1–10 (UAD kept in backend)               | **Done** |
| Auto listing (Audience-First) + grounding/cliché fixes | **Done** |
| `./run.sh` one-command setup/start + `QUICKSTART.md`   | **Done** |
| Real training data collection (~2,600 photos)         | In progress (Jimmy)   |
| home-report-ai integration (skip Stage 1 + accept groups) | Not started       |
| Low-confidence VLM re-check for classifier accuracy   | Proposed, not started |
| Staging API integration                               | Deferred              |

## How to make changes

- Touch one module at a time. Cross-module changes (frontend + agent + home-report)
  should land as a single coordinated commit with all three updated.
- When adding a new endpoint to a service, update the consumer in the same change.
- Don't introduce new dependencies in agent or bi without checking the venv
  is right (each module has its own .venv).
- See each subproject's own `CLAUDE.md` for module-specific rules.

## People

- **Jimmy** (you, this user): primary builder. Boston/Cambridge office, onsite Mon-Thu.
- **George**: boss/PM. Drives product positioning via WeChat-shared references
  (plan0.ai, LocateAlpha, HouseQuest). Wants self-hosted CV (not VLM-dependent)
  for long-term cost and control.
- **Lawrence**: coworker.
