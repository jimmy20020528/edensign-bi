#!/usr/bin/env bash
# run.sh — one-command setup + launch for the whole Edensign stack.
#
#   ./run.sh setup     create a venv + install deps for every module (idempotent)
#   ./run.sh start     start all services, run health checks, print the wizard URL
#   ./run.sh stop      stop all services
#   ./run.sh restart   stop + start
#   ./run.sh status    show each service's health
#   ./run.sh           setup, then start
#
# Fresh machine: clone → fill in .env (API keys) → ./run.sh
#
# Ports are overridable via env (e.g. HR_PORT=8011 ./run.sh start) for hosts where
# the default is taken (this RunPod pod runs nginx on 8001).
#
# SKIP_CV=1 ./run.sh start   → bring up bi+agent+home-report only, and leave an
#   already-running cv-models on :8003 completely untouched (start/stop/setup never
#   touch it). Use when :8003 is a live production endpoint that must not go down.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

BI_PORT="${BI_PORT:-8000}"
HR_PORT="${HR_PORT:-8001}"
AGENT_PORT="${AGENT_PORT:-8002}"
CV_PORT="${CV_PORT:-8003}"
# SKIP_CV=1 → cv-models is managed elsewhere (e.g. an already-deployed, live endpoint
# on this port). run.sh will NOT start, restart, reinstall, or stop it — it only points
# the agent at the existing service. Use this when :8003 is part of a production service
# that must not blink.
SKIP_CV="${SKIP_CV:-}"
PY="${PYTHON:-python3}"
LOG_DIR="$ROOT/.run-logs"
mkdir -p "$LOG_DIR"

log()  { printf '\033[36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
err()  { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; }

ensure_env() {
  if [ ! -f .env ]; then
    if [ -f .env.example ]; then
      cp .env.example .env
      warn ".env created from .env.example — FILL IN YOUR API KEYS (OPENAI_API_KEY, etc.) before starting."
    else
      err "no .env or .env.example found."; return 1
    fi
  fi
}

setup_module() {  # label dir
  local label="$1" dir="$2"
  [ -f "$dir/requirements.txt" ] || { warn "$label: no requirements.txt — skipping"; return 0; }
  if [ ! -d "$dir/.venv" ]; then
    log "$label: creating venv + installing deps (cv-models pulls torch — can take a few minutes)…"
    "$PY" -m venv "$dir/.venv" || { err "$label: venv creation failed"; return 1; }
  fi
  "$dir/.venv/bin/pip" install -q --upgrade pip >/dev/null 2>&1
  "$dir/.venv/bin/pip" install -q -r "$dir/requirements.txt" || { err "$label: pip install failed"; return 1; }
  ok "$label deps ready"
}

cmd_setup() {
  ensure_env || return 1
  setup_module "bi" "."
  if [ -n "$SKIP_CV" ]; then
    warn "cv-models: SKIP_CV set — leaving its venv/deps untouched (managed externally)"
  else
    setup_module "cv-models" "cv-models"
  fi
  setup_module "home-report-ai" "home-report-ai"
  setup_module "agent" "agent"
  ./.venv/bin/python -c "import pgeocode" 2>/dev/null || ./.venv/bin/pip install -q pgeocode  # bi market estimator
  ok "setup complete — now: ./run.sh start"
}

free_port() {  # port
  local pid
  pid="$(ss -ltnp 2>/dev/null | grep ":$1 " | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)"
  [ -n "${pid:-}" ] && kill -9 "$pid" 2>/dev/null && sleep 1 || true
}

start_svc() {  # name dir port cmd...
  local name="$1" dir="$2" port="$3"; shift 3
  free_port "$port"
  ( cd "$dir" && setsid nohup "$@" >"$LOG_DIR/$name.log" 2>&1 </dev/null & )
  log "$name → :$port"
}

wait_health() {  # url name
  local i
  for i in $(seq 1 60); do
    curl -s --max-time 2 "$1" >/dev/null 2>&1 && { ok "$2 healthy"; return 0; }
    sleep 2
  done
  warn "$2 not healthy yet — check $LOG_DIR/$2.log"; return 1
}

cmd_start() {
  ensure_env || return 1
  local cv_override="${CV_MODELS_BASE:-}"   # capture command-line override before .env can clobber it
  set -a; source .env; set +a

  # Where the agent finds cv-models. By default run.sh wires it to the local one it
  # starts. Pass CV_MODELS_BASE=https://<pod>-8003.proxy.runpod.net to point at an
  # external/live cv-models instead — that also implies SKIP_CV (no local one started).
  if [ -n "$cv_override" ]; then
    CV_BASE="$cv_override"; SKIP_CV=1
    log "cv-models: using external CV_MODELS_BASE=$CV_BASE (no local cv-models)"
  else
    CV_BASE="http://localhost:$CV_PORT"
  fi

  # Postgres is optional — bi falls back to LLM-only market estimation without it.
  if command -v pg_ctlcluster >/dev/null 2>&1; then
    pg_lsclusters -h 2>/dev/null | awk 'NF>=2{print $1,$2}' | while read -r v c; do
      pg_ctlcluster "$v" "$c" start 2>/dev/null || true
    done
  fi
  export OPENAI_MODEL_ESTIMATOR="${OPENAI_MODEL_ESTIMATOR:-gpt-4o-mini}"

  # cv-models inserts its own scripts/ path; bi needs scripts/ on PYTHONPATH.
  if [ -n "$SKIP_CV" ]; then
    log "cv-models: SKIP_CV set — NOT touching the live service on :$CV_PORT"
  else
    start_svc "cv-models" "cv-models" "$CV_PORT" \
      "$ROOT/cv-models/.venv/bin/uvicorn" app.main:app --host 0.0.0.0 --port "$CV_PORT"
  fi
  start_svc "home-report-ai" "home-report-ai" "$HR_PORT" \
    "$ROOT/home-report-ai/.venv/bin/uvicorn" src.api.main:app --host 0.0.0.0 --port "$HR_PORT"
  # bi is the single public gateway — give it the upstream URLs so it can proxy
  # /classify-rooms, /walkthrough, /report, /pipeline/run to the right service.
  PYTHONPATH="$ROOT/scripts:$ROOT" \
  AGENT_BASE="http://localhost:$AGENT_PORT" \
  CV_MODELS_BASE="$CV_BASE" \
  HOME_REPORT_BASE="http://localhost:$HR_PORT" \
    start_svc "bi" "." "$BI_PORT" \
    "$ROOT/.venv/bin/uvicorn" app.main:app --host 0.0.0.0 --port "$BI_PORT"
  # The agent proxies to the others — give it their base URLs explicitly.
  BI_BASE="http://localhost:$BI_PORT" \
  HOME_REPORT_BASE="http://localhost:$HR_PORT" \
  CV_MODELS_BASE="$CV_BASE" \
  AGENT_BASE="http://localhost:$AGENT_PORT" \
    start_svc "agent" "agent" "$AGENT_PORT" "$ROOT/agent/.venv/bin/python" tools/server.py

  echo
  if [ -n "$SKIP_CV" ]; then
    curl -s --max-time 5 "$CV_BASE/health" >/dev/null 2>&1 \
      && ok "cv-models reachable at $CV_BASE (external, left running)" \
      || warn "cv-models NOT reachable at $CV_BASE — the agent needs it for classification"
  else
    wait_health "http://localhost:$CV_PORT/health"    "cv-models"
  fi
  wait_health "http://localhost:$HR_PORT/health"    "home-report-ai"
  wait_health "http://localhost:$BI_PORT/health"    "bi"
  wait_health "http://localhost:$AGENT_PORT/health" "agent"
  echo
  ok "All services up."
  echo "   Open the Listing Wizard:  http://localhost:$BI_PORT/ui/wizard.html"
  echo "   Logs: $LOG_DIR/   ·   Stop: ./run.sh stop"
}

cmd_stop() {
  local ports="$BI_PORT $HR_PORT $AGENT_PORT"
  if [ -n "$SKIP_CV" ]; then
    warn "cv-models: SKIP_CV set — NOT stopping the live service on :$CV_PORT"
  else
    ports="$ports $CV_PORT"
  fi
  for p in $ports; do free_port "$p"; done
  ok "stopped: $ports"
}

cmd_status() {
  local name port
  for pair in "bi:$BI_PORT" "home-report-ai:$HR_PORT" "agent:$AGENT_PORT" "cv-models:$CV_PORT"; do
    name="${pair%%:*}"; port="${pair##*:}"
    if curl -s --max-time 2 "http://localhost:$port/health" >/dev/null 2>&1; then
      ok "$name (:$port) up"
    else
      err "$name (:$port) down"
    fi
  done
}

case "${1:-}" in
  setup)   cmd_setup ;;
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status)  cmd_status ;;
  "")      cmd_setup && cmd_start ;;
  *)       echo "usage: ./run.sh [setup|start|stop|restart|status]"; exit 1 ;;
esac
