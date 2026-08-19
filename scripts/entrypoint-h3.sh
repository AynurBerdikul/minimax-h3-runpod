#!/usr/bin/env bash
set -euo pipefail

echo "[H3] Preparing RunPod Serverless worker"

if [ ! -d /runpod-volume ]; then
  echo "[H3] ERROR: /runpod-volume is not mounted."
  echo "[H3] Attach a RunPod Network Volume to this Serverless endpoint."
  exit 20
fi

python /opt/h3/scripts/prepare_models.py
python /opt/h3/scripts/download_workflows.py
python /opt/h3/scripts/validate_setup.py

exec /start.sh
