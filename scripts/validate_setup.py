from __future__ import annotations
import os, sys
from pathlib import Path

ROOT = Path(os.getenv("H3_MODEL_ROOT", "/runpod-volume/models"))
REQUIRED = [
    ROOT / "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    ROOT / "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
    ROOT / "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    ROOT / "vae/minimax_h3_video_vae_fp16.safetensors",
    ROOT / "vae/minimax_h3_audio_vae_fp32.safetensors",
]

def main():
    missing = [p for p in REQUIRED if not p.exists() or p.stat().st_size < 1024 * 1024]
    if missing:
        print("[H3] ERROR: missing/truncated model files:", file=sys.stderr)
        for p in missing:
            print(f"  - {p}", file=sys.stderr)
        raise SystemExit(22)
    try:
        import torch
        print(f"[H3] torch={torch.__version__} cuda={torch.version.cuda}")
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            print(f"[H3] GPU={props.name}, VRAM={props.total_memory / 1024**3:.1f} GiB")
    except Exception as exc:
        print(f"[H3] WARNING: GPU diagnostic failed: {exc}")
    print("[H3] model storage validated")

if __name__ == "__main__":
    main()
