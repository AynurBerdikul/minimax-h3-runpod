#!/usr/bin/env bash
set -euo pipefail

# The upstream compiler names these H3IR_LLM_KEY and H3IR_LLM_TIMEOUT. Keep the
# public worker contract requested for this project without ever printing a secret.
if [ -n "${H3IR_LLM_API_KEY:-}" ] && [ -z "${H3IR_LLM_KEY:-}" ]; then
  export H3IR_LLM_KEY="${H3IR_LLM_API_KEY}"
fi
if [ -z "${H3IR_LLM_TIMEOUT:-}" ]; then
  export H3IR_LLM_TIMEOUT="${H3IR_TIMEOUT_SECONDS:-180}"
fi

echo "[H3] Validating pre-populated Network Volume..."

if [ ! -d /runpod-volume ]; then
  echo "[H3] ERROR: /runpod-volume is not mounted."
  exit 20
fi

python /opt/h3/scripts/validate_volume.py

if [ -n "${H3IR_LLM_URL:-}" ]; then
  echo "[H3IR] compiler installed; external vision endpoint configured."
else
  echo "[H3IR] compiler installed; vision endpoint not configured. Raw H3 workflows remain available."
fi

echo "[H3] Network Volume is valid. Starting official RunPod worker..."
exec /start.sh
