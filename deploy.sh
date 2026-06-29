#!/usr/bin/env bash
# deploy.sh — start the Edensign backend gateway on a RunPod CPU pod.
#
# bi gateway on :80, agent + home-report internal. Thin wrapper over run.sh — see
# DEPLOY.md. Ports overridable: BI_PORT=8188 ./deploy.sh, AGENT_PORT=… HR_PORT=…
#
#   ./deploy.sh                 setup + start (light: NO cv-models; classify is the
#                               separate :8003 pod, never touched)
#   WALKTHROUGH=1 ./deploy.sh   also run a local demo cv-models on :8188 (internal) so
#                               /walkthrough works through :80 (installs CPU torch)
#   ./deploy.sh start|stop|restart|status
set -euo pipefail
cd "$(dirname "$0")"

if [ -n "${WALKTHROUGH:-}" ]; then
  # cv-models on :8188 (internal) for /walkthrough; bi:80 proxies to it. run.sh only
  # touches :8188, so the live classify on :8003 is left alone.
  exec env BI_PORT="${BI_PORT:-80}" HR_PORT="${HR_PORT:-8011}" CV_PORT="${CV_PORT:-8188}" ./run.sh "${1:-}"
else
  exec env BI_PORT="${BI_PORT:-80}" HR_PORT="${HR_PORT:-8011}" SKIP_CV=1 ./run.sh "${1:-}"
fi
