from __future__ import annotations
import os
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(os.getenv("H3_WORKFLOW_ROOT", "/runpod-volume/workflows"))
URLS = {
    "video_minimax_h3_t2v.json":
        "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/video_minimax_h3_t2v.json",
    "video_minimax_h3_i2v.json":
        "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/video_minimax_h3_i2v.json",
    "video_minimax_h3_r2v.json":
        "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/video_minimax_h3_r2v.json",
}

def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    for name, url in URLS.items():
        target = ROOT / name
        if target.exists() and target.stat().st_size > 1000:
            print(f"[H3] workflow present: {target}")
            continue
        print(f"[H3] downloading official workflow: {name}")
        with urlopen(url, timeout=90) as response:
            data = response.read()
        tmp = target.with_suffix(".json.part")
        tmp.write_bytes(data)
        os.replace(tmp, target)

if __name__ == "__main__":
    main()
