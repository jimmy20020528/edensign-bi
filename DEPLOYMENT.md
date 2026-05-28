# Edensign — RunPod Deployment Guide

## Pod Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 4 vCPU | 8 vCPU |
| RAM | 16 GB | 32 GB |
| GPU | None (CPU pod works) | Any NVIDIA (speeds up DINOv2) |
| Disk | 30 GB | 50 GB |
| OS | Ubuntu 22.04 | Ubuntu 22.04 |

> **Note:** GPU is optional. DINOv2 inference for 30 photos runs in ~8s on CPU.

---

## Step 1 — Create the RunPod Pod

1. Go to [runpod.io](https://runpod.io) → **Pods** → **Deploy**
2. Choose a template: **RunPod PyTorch 2.1** (has Python 3.11 + CUDA pre-installed)
3. Select instance type (CPU-only is fine: `2x CPU, 16GB RAM`)
4. Under **Expose TCP Ports**, add all five:

   ```
   5173, 8000, 8001, 8002, 8003
   ```

5. Deploy the pod.

---

## Step 2 — SSH Into the Pod

```bash
ssh root@<pod-ip> -p <ssh-port> -i ~/.ssh/id_rsa
# Or use the "Connect" button in RunPod UI → "SSH over exposed TCP"
```

---

## Step 3 — Clone the Repo

```bash
cd /workspace
git clone https://github.com/jimmy20020528/edensign-bi.git
cd edensign-bi
git checkout runpod-deploy
```

---

## Step 4 — Set Up `.env`

```bash
cp .env.example .env
nano .env   # fill in all keys
```

Required keys (minimum to run the full pipeline):

| Key | Where to get it |
|-----|----------------|
| `OPENAI_API_KEY` | platform.openai.com → API keys |
| `GEMINI_API_KEY` | aistudio.google.com |
| `RUNPOD_API_KEY` | runpod.io → Settings → API Keys |
| `RUNPOD_STAGING_ENDPOINT` | runpod.io → Serverless → your staging endpoint ID |
| `DB_*` | Your PostgreSQL instance (see Step 5) |
| `GOOGLE_MAPS_API_KEY` | console.cloud.google.com (Geocoding API) |
| `WALKSCORE_API_KEY` | walkscore.com/professional/api.php |
| `FRED_API_KEY` | fred.stlouisfed.org/docs/api/api_key.html (free) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | IAM user with s3:PutObject on `edensign-content` bucket |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` | supabase.com → Project Settings → API |

`GREATSCHOOLS_API_KEY`, `CENSUS_API_KEY`, `RENTCAST_API_KEY` are optional — those data sources fall back to empty if keys are missing.

---

## Step 5 — PostgreSQL

The BI service needs a PostgreSQL + PostGIS database. Options:

**Option A: Supabase (easiest)**
Supabase is Postgres. Use the connection string from Supabase → Project Settings → Database:
```
DB_HOST=db.xxxx.supabase.co
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=<your-password>
DB_NAME=postgres
```

**Option B: RunPod sidecar**
```bash
apt-get install -y postgresql postgis
service postgresql start
sudo -u postgres psql -c "CREATE USER edensign WITH PASSWORD 'your_pass';"
sudo -u postgres psql -c "CREATE DATABASE edensign_bi OWNER edensign;"
sudo -u postgres psql edensign_bi -c "CREATE EXTENSION postgis;"
```

---

## Step 6 — Start All Services

```bash
cd /workspace/edensign-bi
bash start.sh
```

First run installs all Python dependencies (~5-10 minutes for cv-models due to torch). Subsequent runs skip install and start in ~30 seconds.

> **Disk space:** First run downloads ~5–10 GB of packages. Ensure `/workspace` has at least 20 GB free before starting.

Expected output:
```
✓ Environment loaded
→ Creating venv for bi...
✓ bi deps installed
...
✓ bi          → port 8000 (PID 1234)
✓ home-report → port 8001 (PID 1235)
✓ cv-models   → port 8003 (PID 1236)
✓ agent       → port 8002 (PID 1237)
✓ frontend    → port 5173 (PID 1238)
All services running. Ctrl+C to stop.
```

---

## Step 7 — Access the App

In RunPod UI, each exposed port gets a public URL like:
```
https://<pod-id>-5173.proxy.runpod.net
```

Open **port 5173** URL in your browser → Listing Wizard.

> **Note:** RunPod's reverse proxy handles HTTPS termination. The frontend is served over plain HTTP internally; the proxy presents it as HTTPS to the browser.

> **Important:** The frontend calls the agent service (port 8002) and BI service (port 8000) directly. After deploying, update `apiBase` and `stagingBase` in `frontend/build_wizard.py` to the RunPod public URLs, then rebuild `wizard.html`.

```javascript
// In build_wizard.py, update these two lines:
const [apiBase] = useState("https://<pod-id>-8002.proxy.runpod.net");
const [stagingBase] = useState("https://<pod-id>-8000.proxy.runpod.net");
```

Then rebuild:
```bash
cd /workspace/edensign-bi
python3 frontend/build_wizard.py
```

---

## Health Checks

```bash
curl http://localhost:8000/health   # bi
curl http://localhost:8001/health   # home-report-ai
curl http://localhost:8002/health   # agent
curl http://localhost:8003/health   # cv-models
```

All should return `{"status": "ok"}`.

---

## Keeping Services Running (Optional)

To survive SSH disconnection:

```bash
# Install tmux (usually pre-installed)
tmux new -s edensign
bash start.sh
# Detach: Ctrl+B then D
# Re-attach: tmux attach -t edensign
```

---

## Port Reference

| Port | Service | Caller |
|------|---------|--------|
| 5173 | Frontend (static) | Browser |
| 8000 | BI service | Browser (staging modal), Agent |
| 8001 | home-report-ai | Agent only |
| 8002 | Agent / pipeline | Browser (wizard) |
| 8003 | cv-models | Agent only |
