# Edensign — Quick Start & Deployment

Everything you need to run the stack locally and deploy it as a service.
The stack is five processes: **bi** (8000), **home-report-ai** (8001),
**agent** (8002), **cv-models** (8003), and the **Listing Wizard** (a static page
that bi serves at `/ui/`).

---

## 1. Local — one command

```bash
git clone <repo> && cd edensign-bi
cp .env.example .env          # then fill in API keys (see §4)
./run.sh                      # = setup (venvs + deps) + start everything
```

Open the Listing Wizard: <http://localhost:8000/ui/wizard.html>

`./run.sh` with no argument runs `setup` then `start`. Subcommands:

| command | what it does |
|---|---|
| `./run.sh setup`   | create a venv + install deps for every module (idempotent) |
| `./run.sh start`   | start all services, health-check them, print the wizard URL |
| `./run.sh stop`    | stop all services |
| `./run.sh restart` | stop + start |
| `./run.sh status`  | show each service's health |

First run downloads models (cv-models pulls **DINOv2** + torch — a few minutes,
~2 GB). Service logs are written to `.run-logs/`.

Override a port when its default is taken (this RunPod pod runs nginx on 8001, so
home-report-ai is started elsewhere):

```bash
HR_PORT=8011 ./run.sh start      # also: BI_PORT, AGENT_PORT, CV_PORT, PYTHON
```

---

## 2. Prerequisites

`run.sh` is written for **Linux** (uses `ss`, `setsid`). On a fresh Ubuntu/Debian:

```bash
sudo apt-get update && sudo apt-get install -y \
  python3 python3-venv python3-pip \
  curl iproute2 build-essential git
# optional: Postgres via Docker (see §5), or a managed Postgres
```

- **Python 3.10+** (this repo's venvs are 3.12). `cv-models` needs torch — CPU
  works; a CUDA GPU makes classification/walk-through much faster (the code
  auto-detects CUDA, no config change).
- `iproute2` provides `ss` (port detection); `curl` is used for health checks.
- macOS works for dev but `run.sh`'s `ss`/`setsid` differ — prefer Linux for
  anything beyond a quick look.

---

## 3. Services & ports

| service | port | role |
|---|---|---|
| **bi**            | 8000 | ZIP market analysis, comps/CMA, neighborhood, buyer appeal, listing copy. **Serves the wizard at `/ui/` and proxies the wizard's agent calls.** This is the only service that needs to be public. |
| **home-report-ai**| 8001 | per-photo quality/condition (UAD) + per-room suggestions (VLM) |
| **agent**         | 8002 | orchestration: `/pipeline/run`, and proxies `/classify-rooms` + `/walkthrough` to cv-models |
| **cv-models**     | 8003 | DINOv2 room classifier + instance grouping + photo walk-through ordering |

The agent reads the others' URLs from env (`BI_BASE`, `HOME_REPORT_BASE`,
`CV_MODELS_BASE`) — `run.sh` sets these for you. Postgres is **optional**; without
it, bi falls back to LLM-only market estimation.

---

## 4. Environment (`.env`)

Single source of truth for every service (they all read this one file).

| key | needed for |
|---|---|
| **`OPENAI_API_KEY`** | **required** — all narratives, listing copy, and the LLM market fallback |
| `GEMINI_API_KEY` | home-report-ai's photo VLM (quality/condition + features) |
| `WALKSCORE_API_KEY` | neighborhood walk/transit/bike scores |
| `FRED_API_KEY`, `CENSUS_API_KEY` | macro indicators + address→ZIP geocoding |
| `DB_HOST/PORT/USER/PASSWORD/NAME` | Postgres (optional) |

With placeholder keys the services **start fine but fail at request time**, so put
in at least a real `OPENAI_API_KEY` (+ `GEMINI_API_KEY` for the home report) before
expecting real output.

Data-source notes (so output is predictable):
- **Neighborhood** uses **OpenStreetMap** (Nominatim + Overpass, no key).
  `GOOGLE_MAPS_API_KEY` in `.env` is a placeholder/invalid and is **not used**.
- **Comps** use Redfin's public `gis-csv` (unofficial; degrades gracefully if
  rate-limited/blocked). No key.
- **School ratings** need NCES or a real GreatSchools key. NCES blocks datacenter
  IPs and the GreatSchools key is a placeholder, so on such hosts the school
  *score* is omitted (nearby school *names* still show via OSM).

---

## 5. Using it

**Via the wizard** (`/ui/wizard.html`): upload photos + an address. Room
classification runs automatically in the background; edit/delete rooms if needed,
then **Run Pipeline** for the full package (market, neighborhood, comps, buyer
appeal, property assessment 1–10, walk-through order, auto-written listing).

**Via the API** (for integration). The whole package in one call — hit bi:8000,
which proxies `/pipeline/run` to the agent (so a single origin is enough, same as
the wizard):

```bash
curl -X POST http://localhost:8000/pipeline/run \
  -F address="42 Tappan St, Everett, MA 02149" \
  -F bedrooms=3 -F bathrooms=2 -F sqft=1500 \
  -F files=@photo1.jpg -F files=@photo2.jpg
# → { zipcode, address, home_report, bi_analysis, bi_explain, walkthrough, ... }
```

Useful individual endpoints:

| endpoint | purpose |
|---|---|
| `GET  :8000/analyze/by-zipcode?zipcode=02149` | ranked staging styles + market |
| `POST :8000/analyze/neighborhood` `{address}` | amenities + walkability + narrative |
| `POST :8000/analyze/comps` `{zipcode,sqft,...}`| CMA: comps + range + highlights |
| `POST :8000/analyze/buyer-appeal` `{home_report,specs}` | buyer-appeal narrative |
| `POST :8000/listing/write` `{style,home_report,...}` | listing copy |
| `POST :8003/classify-rooms` (multipart) | room types + instance groups |

(Full request/response shapes: see `app/main.py` and each service's `CLAUDE.md`.)

---

## 6. Deploy as a service (production)

The app services have **no Docker images** (the bundled `docker-compose.yml` only
runs Postgres). Deploy them as long-running processes behind a reverse proxy.

### 6a. One public origin → bi

The wizard calls the API at its **own origin** (`window.location.origin`). bi
already serves the wizard *and* proxies its agent calls, so **only bi needs to be
public** — put nginx in front of bi:8000 and route everything to it. agent,
home-report-ai, and cv-models stay on `127.0.0.1`.

```nginx
# /etc/nginx/sites-available/edensign
server {
    listen 443 ssl;
    server_name app.edensign.io;
    ssl_certificate     /etc/letsencrypt/live/app.edensign.io/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/app.edensign.io/privkey.pem;

    client_max_body_size 100m;       # photo uploads
    proxy_read_timeout   300s;       # /pipeline/run fans out to VLMs

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

The wizard is then `https://app.edensign.io/ui/wizard.html`.

### 6b. Run the services under systemd (auto-restart + survive reboot)

Bind each service to `127.0.0.1` (only nginx is public). One unit per service —
example for bi (repeat for the others, changing `ExecStart`/`WorkingDirectory`):

```ini
# /etc/systemd/system/edensign-bi.service
[Unit]
Description=Edensign bi
After=network.target
[Service]
WorkingDirectory=/opt/edensign-bi
EnvironmentFile=/opt/edensign-bi/.env
Environment=PYTHONPATH=/opt/edensign-bi/scripts:/opt/edensign-bi
ExecStart=/opt/edensign-bi/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
[Install]
WantedBy=multi-user.target
```

- **cv-models**: `WorkingDirectory=/opt/edensign-bi/cv-models`,
  `ExecStart=…/cv-models/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8003`
- **home-report-ai**: `WorkingDirectory=…/home-report-ai`,
  `ExecStart=…/home-report-ai/.venv/bin/uvicorn src.api.main:app --host 127.0.0.1 --port 8001`
- **agent**: `WorkingDirectory=…/agent`,
  `ExecStart=…/agent/.venv/bin/python tools/server.py`, and add
  `Environment=BI_BASE=http://127.0.0.1:8000` `HOME_REPORT_BASE=http://127.0.0.1:8001`
  `CV_MODELS_BASE=http://127.0.0.1:8003`

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now edensign-bi edensign-cv-models edensign-home-report-ai edensign-agent
```

> `run.sh` is for dev/quick demos (it backgrounds processes with `nohup`). For a
> real deployment use systemd (above) so services restart on crash/reboot.

### 6c. Database (optional)

bi runs without Postgres (LLM-only market estimation). To use a real DB, point
`DB_*` at a managed Postgres **or** run the bundled one:

```bash
docker compose up -d db    # Postgres 16 + PostGIS, applies schema.sql on first run
```

### 6d. First-deploy checklist

1. `git clone` to `/opt/edensign-bi`, `cp .env.example .env`, fill keys (§4).
2. `./run.sh setup` (builds the four venvs + installs deps).
3. (optional) `docker compose up -d db`.
4. Install the systemd units (§6b), `enable --now`.
5. Install the nginx site (§6a), get TLS (`certbot`), `nginx -t && systemctl reload nginx`.
6. Verify: `curl https://app.edensign.io/health` and open `…/ui/wizard.html`.

---

## 7. Troubleshooting

- **Port in use** → override: `HR_PORT=8011 ./run.sh start`.
- **A service won't start** → read `.run-logs/<service>.log` (dev) or
  `journalctl -u edensign-<service>` (prod).
- **bi logs "Database unavailable"** → expected without Postgres; LLM-only mode.
- **Wizard calls 404 / fail** → it must be opened on the **bi origin**
  (`…:8000/ui/…` or your nginx domain) so same-origin proxying works. Opening the
  HTML file directly, or from a separate static server, breaks the API calls.
- **Listing/narratives are empty or error** → check `OPENAI_API_KEY` is real.
- **Classification slow on first request** → cv-models loads DINOv2 lazily at
  startup; the very first request after boot waits for the model.
