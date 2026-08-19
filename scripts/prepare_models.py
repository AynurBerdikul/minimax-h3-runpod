from __future__ import annotations
import os, sys, shutil
from pathlib import Path
from huggingface_hub import hf_hub_download

REPO_ID = "Comfy-Org/MiniMax-H3"
ROOT = Path(os.getenv("H3_MODEL_ROOT", "/runpod-volume/models"))
FILES = {
    "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors":
        "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors":
        "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
    "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors":
        "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "vae/minimax_h3_video_vae_fp16.safetensors":
        "vae/minimax_h3_video_vae_fp16.safetensors",
    "vae/minimax_h3_audio_vae_fp32.safetensors":
        "vae/minimax_h3_audio_vae_fp32.safetensors",
}

def main():
    if os.getenv("ACCEPT_MINIMAX_H3_LICENSE") != "YES":
        print("[H3] ERROR: Set ACCEPT_MINIMAX_H3_LICENSE=YES only after reviewing the MiniMax H3 license.", file=sys.stderr)
        raise SystemExit(21)

    token = os.getenv("HF_TOKEN") or None
    ROOT.mkdir(parents=True, exist_ok=True)

    for repo_path, relative_path in FILES.items():
        target = ROOT / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size > 1024 * 1024:
            print(f"[H3] present: {target}")
            continue
        print(f"[H3] downloading {repo_path}")
        cached = hf_hub_download(repo_id=REPO_ID, filename=repo_path, token=token)
        tmp = target.with_suffix(target.suffix + ".part")
        if tmp.exists():
            tmp.unlink()
        shutil.copy2(cached, tmp)
        os.replace(tmp, target)
        print(f"[H3] saved: {target}")

if __name__ == "__main__":
    main()
