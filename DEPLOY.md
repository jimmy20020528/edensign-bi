# Backend Deployment — RunPod (CPU pod)

How to run the Edensign backend (the **bi gateway + agent + home-report**) on a
RunPod CPU pod, alongside the existing **classification** service. Written for the
current architecture; pairs with `run.sh` and `API.md`.

## TL;DR

```bash
git clone -b deploy/backend-runpod https://github.com/jimmy20020528/edensign-bi.git
cd edensign-bi
# put .env here (transfer it — see §3b), then ONE command:
./deploy.sh                  # gateway on :80   (or: WALKTHROUGH=1 ./deploy.sh)
```

Expose port **80** on the pod → consumer calls `https://<pod>-80.proxy.runpod.net`.
No version flags, no manual torch — `deploy.sh` auto-handles Python/CPU-torch/ports.

---

## 1. Architecture (two independent services)

| service | where | port | notes |
|---|---|---|---|
| **Classification** | its own RunPod pod (already deployed) | 8003 | URL-based `POST /classify-rooms {image_urls}` — see `API.md`. **Leave it alone.** |
| **bi gateway** (+ agent + home-report) | this CPU pod | **80** (public) | analysis, comps, neighborhood, buyer-appeal, listing, report, persistence |

The frontend (rebuilt by the consumer) calls **two base URLs**: the classify pod
for `/classify-rooms`, and this gateway for everything else.

### No Docker inside the pod
A RunPod **Pod is itself a container** — you can't reliably run Docker (no daemon /
docker-in-docker) inside it. So we run the services **directly** with `run.sh`
(per-module venvs + uvicorn). Docker images are only for RunPod **Serverless** or a
real VM/EC2 — built elsewhere, not inside a pod.

---

## 2. Why this is light (no GPU / no torch here)

This pod runs only **bi + agent + home-report**, all light async Python services
(FastAPI/httpx/sklearn/openai). **cv-models (DINOv2 + torch) does NOT run here** —
classification is the separate 8003 pod. `SKIP_CV=1` skips its venv entirely, so:

- No torch download, no GPU needed.
- Setup finishes in a few minutes; fits easily in 8 vCPU / 16 GB / 100 GB.
- home-report calls Gemini over the network (no local model).

Postgres is optional — without it bi uses the LLM market estimator (any ZIP works;
the DB is only seeded for a couple of Boston ZIPs and bi falls back to the LLM for
the rest).

---

## 3. Steps

### 3a. Clone to local disk
RunPod's network volume (`/workspace` on some pods) is ~100× slower for the many
small files a venv creates. **Clone to the container disk** (`/root`), not a network
volume.

```bash
cd /root
git clone -b deploy/backend-runpod https://github.com/jimmy20020528/edensign-bi.git
cd /root/edensign-bi
```

### 3b. Put `.env` in place
The backend reads one `.env`. Required/used keys:

- `OPENAI_API_KEY` (analysis narratives + LLM market estimator) — required
- `GEMINI_API_KEY` (home-report photo VLM)
- `WALKSCORE_API_KEY`, `FRED_API_KEY`, `CENSUS_API_KEY`
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` — S3 photo upload (`/upload`)
- `SUPABASE_URL` / `SUPABASE_ANON_KEY` — submission persistence
- (optional) `SUPABASE_DB_URL` — lets bi auto-create the Supabase tables/columns on start
- (optional) `DB_*` — Postgres; omit to run LLM-only

Transfer it from a machine that already has it (don't commit it):

```bash
# on the source machine:
runpodctl send /path/to/.env          # prints a one-time code
# on this pod, in /root/edensign-bi:
runpodctl receive <code>
```

### 3c. Start

```bash
./deploy.sh                  # light: bi gateway on :80 (no cv-models, no torch)
# — or, to also serve /walkthrough through :80 —
WALKTHROUGH=1 ./deploy.sh    # also runs a demo cv-models on :8188 (CPU torch, ~5 min)
```

| command | bi | cv-models | walk-through |
|---|---|---|---|
| `./deploy.sh` | :80 | none (classify is the separate :8003 pod) | not served |
| `WALKTHROUGH=1 ./deploy.sh` | :80 | demo on :8188 (internal) | `:80/walkthrough` works |

Either way the live classify on **:8003 is never touched**. `./deploy.sh
{start\|stop\|restart\|status}` afterwards. Logs in `.run-logs/`.

That's it — no version flags or manual torch. The scripts auto-handle what used to bite:
- **Python** — auto-picks 3.12/3.11/3.10 (some pods default `python3` to 3.8, which has no `venv`).
- **CPU torch** — on a GPU-less host, cv-models installs the CPU `torch` wheel (not the ~2.5 GB CUDA build); then numpy is pinned to 1.26.4 and DINOv2 downloads on first request.
- **Ports** — home-report on **:8011** (RunPod pods often run nginx on :8001), classify left on :8003.
- **Deps** — `boto3` (S3 upload) and `hf_transfer` (DINOv2 download) are in requirements.

Internal services: agent :8002, home-report :8011 (localhost only — not exposed). If a
port is taken, override it: `AGENT_PORT=… HR_PORT=… ./deploy.sh`.

### 3d. Expose + hand off
- RunPod: expose HTTP port **80** (8003 is already exposed for classify).
- Give the consumer two base URLs (see `API.md`):
  - gateway: `https://<this-pod>-80.proxy.runpod.net`
  - classify: `https://<classify-pod>-8003.proxy.runpod.net`

---

## 4. Verify

```bash
./run.sh status                                   # bi/agent/home-report up
curl -s localhost:80/health                       # {"status":"ok"}
curl -s "localhost:80/analyze/by-zipcode?zipcode=02149" | head -c 120   # recommended_styles
curl -s -X POST localhost:80/submissions -H 'Content-Type: application/json' -d '{"address":"test"}'  # {"id":...}
```

From outside: `curl https://<pod>-80.proxy.runpod.net/health`.

---

## 5. Notes & troubleshooting

- **Walk-through**: served on `:80/walkthrough` only when you start with
  `WALKTHROUGH=1 ./deploy.sh` (runs a demo cv-models on :8188 internally; bi proxies
  to it). DINOv2 on CPU works, just slower. With the plain `./deploy.sh` the
  `/walkthrough` route exists but has nothing behind it.
- **`/classify-rooms` via the gateway won't work** against the live 8003 (that pod is
  the URL-based variant; the gateway proxy speaks multipart). Call classify directly
  per `API.md`. Analysis/report/persistence all go through :80.
- **A service won't start** → `.run-logs/<service>.log`.
- **Market analysis empty** → check `OPENAI_API_KEY`; bi logs show `LLM fallback failed`
  if the estimator can't run.
- **Persistence not saving a field** → the Supabase column must exist; either run the
  `alter table` statements (see `API.md`) or set `SUPABASE_DB_URL` to auto-create them.
- **`venv creation failed … ensurepip`** → `run.sh` auto-picks a Python ≥3.10, but if
  none with `venv` is installed: `apt install python3.11-venv` (or set `PYTHON=…`), then
  `rm -rf .venv */.venv && ./deploy.sh`.
- **bi unhealthy, log shows `ModuleNotFoundError`** → a dep is missing; install it into
  the bi venv (`.venv/bin/pip install <pkg>`) and `restart`. (boto3 + hf_transfer are
  already in requirements now.)
- **cv-models stays `ready:false`** → it's loading/downloading DINOv2; check
  `.run-logs/cv-models.log`. If it errors on `hf_transfer` (the env sets
  `HF_HUB_ENABLE_HF_TRANSFER=1`), `cv-models/.venv/bin/pip install hf_transfer` then
  `restart`.
- **`Killed` lines during start/restart** → that's `free_port` replacing the previous
  run's bi/agent (not OOM, if everything ends `healthy`). 8003 is never touched.
- **`/report` returns `405 Not Allowed (nginx)`** → a system nginx holds :8001, so
  home-report never bound it (its `/health` was answered by nginx→bi, looking healthy).
  Run home-report elsewhere: `HR_PORT=8011 …` (now the default in `deploy.sh`). Restart.
- **`/upload` shows RunPod's "Waiting for service to respond"** → the proxy timed out on
  a slow first response (large base64 + S3). Test it on the pod itself
  (`curl localhost:80/upload …`); a real `{"url":...}` there means it works (browsers
  upload fine). A `502 S3 upload failed` means the AWS_* keys/bucket are wrong.
