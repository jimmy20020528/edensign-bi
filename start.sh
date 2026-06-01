#!/usr/bin/env bash
# start.sh — Start all Edensign services on RunPod
# Run from repo root: bash start.sh
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# ── 1. Load environment ─────────────────────────────────────────────────────
if [ ! -f .env ]; then
  echo "ERROR: .env not found. Copy .env.example -> .env and fill in keys."
  exit 1
fi
set -a && source .env && set +a
export PYTHONPATH="$ROOT/scripts:${PYTHONPATH:-}"
export OPENAI_MODEL_ESTIMATOR="${OPENAI_MODEL_ESTIMATOR:-gpt-4o-mini}"
echo "✓ Environment loaded (estimator model: $OPENAI_MODEL_ESTIMATOR)"

# ── 2. PostgreSQL ─────────────────────────────────────────────────────────
if pg_isready -q 2>/dev/null; then
  echo "✓ PostgreSQL already running"
else
  echo "→ Starting PostgreSQL..."
  if command -v pg_ctlcluster &>/dev/null; then
    pg_lsclusters -h 2>/dev/null | awk 'NF>=2 {print $1, $2}' | while read ver cluster; do
      pg_ctlcluster "$ver" "$cluster" start 2>/dev/null && echo "  started cluster $ver/$cluster" || true
    done
  elif command -v pg_ctl &>/dev/null; then
    su -c "pg_ctl start" postgres 2>/dev/null || true
  fi
  sleep 2
  pg_isready -q 2>/dev/null && echo "✓ PostgreSQL up" || echo "WARN: PostgreSQL may not be running — BI will fail"
fi

# ── 3. Venvs ────────────────────────────────────────────────────────────────
install_if_needed() {
  local label="$1" dir="$2"
  if [ ! -d "$dir/.venv" ]; then
    echo "→ Creating venv for $label..."
    python3 -m venv "$dir/.venv"
    "$dir/.venv/bin/pip" install --quiet --upgrade pip
    "$dir/.venv/bin/pip" install -r "$dir/requirements.txt"
    echo "✓ $label deps installed"
  else
    echo "✓ $label venv exists"
  fi
}

install_if_needed bi .
install_if_needed home-report-ai home-report-ai
install_if_needed agent agent
[ -f cv-models/requirements.txt ] && install_if_needed cv-models cv-models || true

# Ensure pgeocode is present (needed by LLM market estimator)
.venv/bin/python -c "import pgeocode" 2>/dev/null \
  || { echo "→ Installing pgeocode..."; .venv/bin/pip install pgeocode -q; }
echo "✓ pgeocode available"

# ── 4. Start services ────────────────────────────────────────────────────────
echo ""
echo "Starting services..."

PYTHONPATH="$ROOT/scripts" .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 &
PID_BI=$!
echo "✓ bi          → port 8000 (PID $PID_BI)"

(cd home-report-ai && .venv/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8001) &
PID_HR=$!
echo "✓ home-report → port 8001 (PID $PID_HR)"

(cd cv-models && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8003) &
PID_CV=$!
echo "✓ cv-models   → port 8003 (PID $PID_CV)"

(cd agent && .venv/bin/python tools/server.py) &
PID_AG=$!
echo "✓ agent       → port 8002 (PID $PID_AG)"

python3 -m http.server 5173 --directory "$ROOT/frontend" &
PID_FE=$!
echo "✓ frontend    → port 5173 (PID $PID_FE)"

trap "echo; echo 'Stopping...'; kill $PID_BI $PID_HR $PID_CV $PID_AG $PID_FE 2>/dev/null" EXIT INT TERM

echo ""
echo "All services running. Ctrl+C to stop."
wait
