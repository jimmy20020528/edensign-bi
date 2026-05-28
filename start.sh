#!/usr/bin/env bash
# start.sh — Start all Edensign services on RunPod
# Run from repo root: bash start.sh
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# ── 1. Load environment ─────────────────────────────────────────────────────
if [ ! -f .env ]; then
  echo "ERROR: .env not found. Copy .env.example -> .env and fill in keys."
  exit 1
fi
set -a && source .env && set +a
echo "✓ Environment loaded"

# ── 2. Create venvs and install requirements ────────────────────────────────
install_if_needed() {
  local svc="$1"
  local dir="${2:-.}"
  if [ ! -d "$dir/.venv" ]; then
    echo "→ Creating venv for $svc..."
    python3 -m venv "$dir/.venv"
    "$dir/.venv/bin/pip" install --quiet --upgrade pip
    "$dir/.venv/bin/pip" install -r "$dir/requirements.txt"
    echo "✓ $svc deps installed"
  else
    echo "✓ $svc venv exists (skipping install)"
  fi
}

install_if_needed bi .
install_if_needed home-report-ai home-report-ai
install_if_needed agent agent
install_if_needed cv-models cv-models

# ── 3. Start services ───────────────────────────────────────────────────────
echo ""
echo "Starting services..."

(.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000) &
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

(cd frontend && python3 -m http.server 5173) &
PID_FE=$!
echo "✓ frontend    → port 5173 (PID $PID_FE)"

trap "echo; echo 'Stopping services...'; kill $PID_BI $PID_HR $PID_CV $PID_AG $PID_FE 2>/dev/null; echo 'Done.'" EXIT INT TERM

echo ""
echo "All services running. Ctrl+C to stop."
wait
