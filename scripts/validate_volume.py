from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(os.getenv("WAN_MODEL_ROOT", os.getenv("H3_MODEL_ROOT", "/runpod-volume/models")))
MANIFEST = Path("/opt/h3/model_manifest.json")

manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
problems = []

for item in manifest["files"]:
    path = ROOT / item["path"]
    expected = int(item["size"])
    if not path.exists():
        problems.append(f"MISSING {path}")
        continue
    actual = path.stat().st_size
    if actual != expected:
        problems.append(f"BAD_SIZE {path}: {actual} != {expected}")

if problems:
    print("[WAN] ERROR: Network Volume is not ready.", file=sys.stderr)
    for line in problems:
        print("[WAN]", line, file=sys.stderr)
    print("[WAN] Refusing to start GPU inference. Populate/verify volume first.", file=sys.stderr)
    raise SystemExit(22)

print(f"[WAN] OK: {len(manifest['files'])} model files, exact sizes verified.")
print(f"[WAN] Total model bytes: {manifest['total_bytes']}")
