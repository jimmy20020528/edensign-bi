#!/usr/bin/env bash
cd /workspace/cv-models
set -a; source /workspace/.env; set +a
export HF_HUB_ENABLE_HF_TRANSFER=0
unset HF_HUB_OFFLINE
export PYTHONPATH="/workspace/cv-models/scripts"
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8003
