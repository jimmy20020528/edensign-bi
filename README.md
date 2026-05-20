# Edensign

Pre-market real estate platform: photos + property location → market analysis, per-room condition report, recommended staging style, and a draft listing description.

The user-facing entry point is the **Listing Wizard** at `bi/frontend/wizard.html`.

---

## Architecture

```
Browser (bi/frontend/wizard.html on port 5173)
    │
    │  POST multipart/form-data: photos + zipcode_or_address
    ▼
agent (port 8002)  ──┬──►  bi (port 8000)        market analysis + listing copy
                    └──►  home-report-ai (8001)  VLM home assessment

cv-models           standalone module — trained classifiers, not yet wired
                    into the pipeline. Future RoMa serving on RunPod.
```

### Services

| Service          | Port | Status        | What it does                                                      |
|------------------|------|---------------|-------------------------------------------------------------------|
| `bi`             | 8000 | Production    | ZIP-level market analysis, listing description writer             |
| `home-report-ai` | 8001 | Production    | 30-photo property → Q/C ratings + per-room rationale + suggestions|
| `agent`          | 8002 | Production    | Orchestrator — wraps bi + home-report-ai for the wizard           |
| `cv-models`      | —    | Standalone    | DINOv2 room classifiers (trained). Not yet integrated.            |

The wizard pipeline (`agent /pipeline/run`) is end-to-end working with bi + home-report-ai. cv-models is in the repo but not called by the pipeline yet.

---

## Quick start (local Linux/Mac)

### 1. Clone and set up environments

Each service has its own Python virtualenv and `requirements.txt`. Postgres is needed for `bi`.

```bash
git clone <this-repo>
cd edensign-repo

# Postgres for bi
cd bi
docker compose up -d
cd ..

# bi
cd bi
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in API keys
deactivate
cd ..

# home-report-ai
cd home-report-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
deactivate
cd ..

# agent
cd agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
deactivate
cd ..
```

### 2. Download large data (optional)

`bi` auto-downloads all external data on first use (Redfin TSV, HMDA county CSVs, NCES state school data, Walkscore, Geocode). You can pre-warm the Redfin file to avoid a ~30s delay on the first request:

```bash
./scripts/download_data.sh
```

All other data downloads on-demand per ZIP/county/state and caches under `bi/data/`.

### 3. Apply database schema

```bash
docker exec -i edensign_bi_db psql -U edensign -d edensign_bi < bi/schema.sql
```

### 4. Start services

```bash
cd bi && source .venv/bin/activate && uvicorn app.main:app --port 8000 &
cd home-report-ai && source .venv/bin/activate && uvicorn src.api.main:app --port 8001 &
cd agent && source .venv/bin/activate && set -a && source ../bi/.env && set +a && python tools/server.py &
cd bi/frontend && python3 -m http.server 5173 &
```

Health: `curl localhost:{8000,8001,8002}/health`
Wizard: `http://localhost:5173/wizard.html`

---

## Wizard pipeline contract

Frontend POSTs to `agent /pipeline/run`. Agent calls bi + home-report-ai in parallel, composes listing copy via `bi /listing/write`, returns unified JSON:

```json
{
  "zipcode": "02134",
  "address": "20 Allston St, Boston, MA",
  "n_photos": 8,
  "home_report": { ... },
  "bi_analysis": { ... },
  "bi_explain": { ... },
  "listing_text": "..."
}
```

Changing this shape requires a frontend update in the same commit.

---

## cv-models (standalone)

Trained DINOv2 + linear probe classifiers:
- **Occupancy**: empty vs furnished (~95%)
- **Furnished room type**: 13 classes (~83%)
- **Empty room type**: 13 classes (~68%)

Trained `.pkl` files are NOT in the repo. Regenerate via `cv-models/README.md`.

### serve_roma/ (RunPod GPU target, not yet built)

Future RoMa indoor matcher for room instance grouping. Placeholder folder, see `cv-models/serve_roma/README.md`.

---

## Cross-service rules

See `CLAUDE.md` for the full DO NOT BREAK list. Quick summary:

1. Ports are contractual (8000/8001/8002/5173).
2. `bi/.env` is the source of truth for shared API keys.
3. `/pipeline/run` response schema is consumed by the wizard frontend.
4. `bi/frontend/wizard.html` is built from `index.html` via `build_wizard.py`.
5. VLM cost is real — don't add new VLM calls casually.
6. Never commit `.env`.

---

## License

Proprietary. Edensign internal.
