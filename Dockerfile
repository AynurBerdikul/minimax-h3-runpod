# Pinned RunPod serverless worker release.
FROM runpod/worker-comfyui:5.8.6-base

USER root

ARG COMFYUI_COMMIT=57500fc5bc92566a63f2046824f522cd55c335ca
ARG OPENH3IR_COMFY_COMMIT=44cde1986c370f85d76287fcf81a66e1901c434e
ARG OPENH3IR_CORE_COMMIT=eb17e14710ca59c291972e6be4f6119933debc5b

RUN git -C /comfyui fetch --depth=1 origin "${COMFYUI_COMMIT}" \
    && git -C /comfyui checkout --detach "${COMFYUI_COMMIT}"

RUN python -m pip install --no-cache-dir --force-reinstall \
      torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0 \
      --index-url https://download.pytorch.org/whl/cu128 \
    && python -m pip install --no-cache-dir -r /comfyui/requirements.txt

RUN python - <<'PY'
import importlib.metadata as md
import torch

cuda = str(torch.version.cuda or "")
assert cuda.startswith("12.8"), f"Expected PyTorch cu128, got CUDA runtime {cuda!r}"
print("torch:", torch.__version__, "cuda:", cuda)
print("comfy-kitchen:", md.version("comfy-kitchen"))
print("comfy-aimdo:", md.version("comfy-aimdo"))
PY

# OpenH3-IR is pinned twice: the ComfyUI node pack and the compiler distribution it imports
# lazily at execution time. The vision model remains outside this image behind H3IR_LLM_URL.
COPY patches /opt/h3/patches

RUN git clone --filter=blob:none https://github.com/ruashots/ComfyUI-OpenH3-IR.git \
      /comfyui/custom_nodes/ComfyUI-OpenH3-IR \
    && git -C /comfyui/custom_nodes/ComfyUI-OpenH3-IR checkout --detach "${OPENH3IR_COMFY_COMMIT}" \
    && git clone --filter=blob:none https://github.com/ruashots/open-h3-ir.git /tmp/open-h3-ir \
    && git -C /tmp/open-h3-ir checkout --detach "${OPENH3IR_CORE_COMMIT}" \
    && git -C /tmp/open-h3-ir apply --check /opt/h3/patches/openh3ir-provider-compat.patch \
    && git -C /tmp/open-h3-ir apply /opt/h3/patches/openh3ir-provider-compat.patch \
    && python -m pip install --no-cache-dir /tmp/open-h3-ir \
    && python -m pip install --no-cache-dir \
      -r /comfyui/custom_nodes/ComfyUI-OpenH3-IR/requirements.txt \
    && echo "OpenH3-IR ComfyUI commit: $(git -C /comfyui/custom_nodes/ComfyUI-OpenH3-IR rev-parse HEAD)" \
    && echo "OpenH3-IR compiler commit: $(git -C /tmp/open-h3-ir rev-parse HEAD)" \
    && rm -rf /tmp/open-h3-ir \
    && rm -rf /comfyui/custom_nodes/ComfyUI-OpenH3-IR/.git

RUN python - <<'PY'
import importlib.metadata as md
import importlib.util
import sys
from pathlib import Path

root = Path("/comfyui/custom_nodes/ComfyUI-OpenH3-IR")
spec = importlib.util.spec_from_file_location(
    "openh3ir_comfy_build_check",
    root / "__init__.py",
    submodule_search_locations=[str(root)],
)
assert spec and spec.loader, "Could not create OpenH3-IR custom-node import spec"
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
import h3ir.compile  # noqa: F401

print("OpenH3-IR custom node import: OK")
print("OpenH3-IR compiler import: OK")
print("open-h3-ir distribution:", md.version("open-h3-ir"))
PY

COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml
COPY model_manifest.json /opt/h3/model_manifest.json
COPY scripts /opt/h3/scripts
COPY remote/handler-h3.py /handler.py

RUN chmod +x /opt/h3/scripts/entrypoint-h3.sh \
    && python -m py_compile /handler.py /opt/h3/scripts/validate_volume.py \
    && python /opt/h3/scripts/test_openh3ir_provider_compat.py

ENV H3_MODEL_ROOT=/runpod-volume/models
ENV H3IR_TIMEOUT_SECONDS=180
ENV H3IR_LOG_COMPILED_PROMPT=0
ENV H3IR_ALLOW_DEGRADED=0
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/opt/h3/scripts/entrypoint-h3.sh"]
