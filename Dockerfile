# Pinned RunPod serverless worker release.
FROM runpod/worker-comfyui:5.8.6-base

USER root

ARG COMFYUI_COMMIT=57500fc5bc92566a63f2046824f522cd55c335ca

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

COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml
COPY model_manifest.json /opt/h3/model_manifest.json
COPY scripts /opt/h3/scripts
COPY remote/handler-h3.py /handler.py

RUN chmod +x /opt/h3/scripts/entrypoint-h3.sh \
    && python -m py_compile /handler.py /opt/h3/scripts/validate_volume.py

ENV H3_MODEL_ROOT=/runpod-volume/models
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/opt/h3/scripts/entrypoint-h3.sh"]
