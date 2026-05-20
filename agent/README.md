# agent — Edensign tool service

FastAPI service on **port 8002** that orchestrates `bi` (8000) and `home-report-ai` (8001) for the Listing Wizard.

## What it does

Two roles:

1. **Wizard backend** — `/pipeline/run` is the one-shot endpoint the Listing Wizard frontend calls. It runs bi + home-report-ai in parallel, then composes the listing description via `bi /listing/write`. Returns a unified JSON the frontend renders.

2. **Langflow tool service** — exposes per-tool endpoints (`/tool/analyze_zipcode`, `/tool/generate_listing`, `/tool/generate_home_report`) that the conversational agent in Langflow calls. The `langflow_component.py` file is the custom component for that agent.

## Endpoints

| Method | Path                            | Purpose                                                  |
|--------|---------------------------------|----------------------------------------------------------|
| GET    | `/health`                       | Liveness check                                            |
| POST   | `/tool/analyze_zipcode`         | Distilled BI staging-style recs for a ZIP                |
| POST   | `/tool/generate_listing`        | Market context for an LLM to compose listing copy        |
| POST   | `/tool/generate_home_report`    | Multipart photo upload → home-report-ai `/report`        |
| POST   | `/pipeline/run`                 | The Wizard's one-shot endpoint (multipart, all-in-one)   |

## Running locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Set BI and home-report URLs
cp .env.example .env
# edit .env if needed (defaults are http://localhost:8000 / 8001)

# Also load bi/.env for OPENAI_API_KEY (used by /pipeline/run for listing)
set -a && source ../bi/.env && set +a

python tools/server.py
# or: uvicorn tools.server:app --port 8002
```

Health check:
```bash
curl localhost:8002/health
```

## Environment

- `BI_BASE` — URL for the bi service (default `http://localhost:8000`)
- `HOME_REPORT_BASE` — URL for home-report-ai (default `http://localhost:8001`)

The listing composition (`_compose_listing_via_bi`) inside `/pipeline/run` calls `${BI_BASE}/listing/write`, which in turn uses `OPENAI_API_KEY` from bi's environment. So bi's `.env` must be loaded into the agent's process for the wizard pipeline to work.

## Files

```
agent/
├── tools/
│   ├── server.py              FastAPI app — endpoints above
│   └── langflow_component.py  Custom Langflow component
├── requirements.txt
└── .env.example
```

## Notes

- The agent does not call cv-models today. When cv-models is integrated, `/pipeline/run` will gain a pre-step that classifies photos and groups by instance before calling home-report-ai with hints.
- 30-photo upload cap is enforced in both `/tool/generate_home_report` and `/pipeline/run`.
- All HTTP calls use httpx async with timeouts (30s for bi reads, 60s for analyze, 90s for explain, 120s for listing, 180s for home report).
