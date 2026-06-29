# Backend Deployment — RunPod (CPU pod)

How to run the Edensign backend (the **bi gateway + agent + home-report**) on a
RunPod CPU pod, alongside the existing **classification** service. Written for the
current architecture; pairs with `run.sh` and `API.md`.

## TL;DR

```bash
cd /root                                    # LOCAL disk, NOT a network volume
git clone -b deploy/backend-runpod https://github.com/jimmy20020528/edensign-bi.git
cd /root/edensign-bi
# put .env here (transfer it — see §3), then:
BI_PORT=80 SKIP_CV=1 ./run.sh               # setup + start; gateway on :80
```

Expose port **80** on the pod → Haodong calls `https://<pod>-80.proxy.runpod.net`.

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
BI_PORT=80 SKIP_CV=1 ./run.sh          # = setup (venvs+deps) + start, health-checked
```

| env | effect |
|---|---|
| `BI_PORT=80` | bi gateway binds :80 (root in-container can bind privileged ports) |
| `SKIP_CV=1` | don't install/start/stop a local cv-models — never touches :8003 |

`./run.sh {start\|stop\|restart\|status}` afterwards. Logs in `.run-logs/`.

Internal services: agent :8002, home-report :8001 (localhost only — not exposed). If
either port is taken, override: `AGENT_PORT=… HR_PORT=… BI_PORT=80 SKIP_CV=1 ./run.sh`.

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

- **Walk-through** isn't served here (it needs cv-models). If needed, run a demo
  cv-models on the spare port (e.g. 8188) — DINOv2 on CPU works, just slower — and
  point the gateway's `CV_MODELS_BASE` at it.
- **`/classify-rooms` via the gateway won't work** against the live 8003 (that pod is
  the URL-based variant; the gateway proxy speaks multipart). Call classify directly
  per `API.md`. Analysis/report/persistence all go through :80.
- **A service won't start** → `.run-logs/<service>.log`.
- **Market analysis empty** → check `OPENAI_API_KEY`; bi logs show `LLM fallback failed`
  if the estimator can't run.
- **Persistence not saving a field** → the Supabase column must exist; either run the
  `alter table` statements (see `API.md`) or set `SUPABASE_DB_URL` to auto-create them.
