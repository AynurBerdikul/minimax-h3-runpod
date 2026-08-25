#!/usr/bin/env bash
set -euo pipefail

echo "[WAN] Validating pre-populated Network Volume..."

if [ ! -d /runpod-volume ]; then
  echo "[WAN] ERROR: /runpod-volume is not mounted."
  exit 20
fi

if [ "${WAN_BOOTSTRAP_VOLUME:-1}" = "1" ]; then
  python /opt/h3/scripts/populate_wan_volume.py
fi

python /opt/h3/scripts/validate_volume.py

echo "[WAN] Network Volume is valid. Starting official RunPod worker..."
exec /start.sh
