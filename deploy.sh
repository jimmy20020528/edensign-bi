#!/usr/bin/env bash
# deploy.sh — start the Edensign backend gateway on a RunPod CPU pod.
#
# bi gateway on :80, agent + home-report internal, NO local cv-models (classification
# is the separate :8003 pod and is never touched). Thin wrapper over run.sh — see
# DEPLOY.md. Ports overridable: BI_PORT=8188 ./deploy.sh, AGENT_PORT=… HR_PORT=…
#
#   ./deploy.sh            setup + start
#   ./deploy.sh start|stop|restart|status
set -euo pipefail
cd "$(dirname "$0")"
exec env BI_PORT="${BI_PORT:-80}" SKIP_CV=1 ./run.sh "${1:-}"
