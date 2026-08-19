#!/usr/bin/env bash
set -euo pipefail

echo "[H3] Validating pre-populated Network Volume..."

if [ ! -d /runpod-volume ]; then
  echo "[H3] ERROR: /runpod-volume is not mounted."
  exit 20
fi

python /opt/h3/scripts/validate_volume.py

echo "[H3] Network Volume is valid. Starting official RunPod worker..."
exec /start.sh
