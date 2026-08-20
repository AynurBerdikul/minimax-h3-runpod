# Pinned RunPod serverless worker release.
FROM runpod/worker-comfyui:5.8.6-base

USER root

# MiniMax H3 was merged into ComfyUI in PR #15224.
# Pin the actual merge commit instead of following master.
ARG COMFYUI_COMMIT=57500fc5bc92566a63f2046824f522cd55c335ca

# The H3 PR did not change requirements.txt. We only move ComfyUI source
# to the verified merge commit and deliberately do NOT pip-upgrade the
# RunPod CUDA/PyTorch stack.
RUN git -C /comfyui fetch --depth=1 origin "${COMFYUI_COMMIT}" \
    && git -C /comfyui checkout --detach "${COMFYUI_COMMIT}"

COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml
COPY model_manifest.json /opt/h3/model_manifest.json
COPY scripts /opt/h3/scripts

# Replace the stock image-only handler with a generic saved-media handler.
# It returns small descriptors while large video/audio/image artifacts are
# copied to the attached Network Volume for the local ComfyUI bridge to fetch.
COPY remote/handler-h3.py /handler.py

RUN chmod +x /opt/h3/scripts/entrypoint-h3.sh \
    && python -m py_compile /handler.py /opt/h3/scripts/validate_volume.py

ENV H3_MODEL_ROOT=/runpod-volume/models
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/opt/h3/scripts/entrypoint-h3.sh"]
