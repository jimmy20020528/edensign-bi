#!/usr/bin/env bash
cd /workspace
set -a; source /workspace/.env; set +a
export PYTHONPATH="/workspace/scripts:/workspace"
export AGENT_BASE="http://localhost:8002"
export HF_HUB_ENABLE_HF_TRANSFER=0
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
