FROM runpod/worker-comfyui:5.8.6-base

USER root
ARG COMFYUI_REF=master

RUN git -C /comfyui fetch --depth=1 origin "${COMFYUI_REF}" \
    && git -C /comfyui checkout FETCH_HEAD \
    && pip install --no-cache-dir -r /comfyui/requirements.txt

RUN pip install --no-cache-dir "huggingface_hub>=0.34,<2"

COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml
COPY scripts /opt/h3/scripts
COPY workflows /opt/h3/workflows

RUN chmod +x /opt/h3/scripts/entrypoint-h3.sh

ENV H3_MODEL_ROOT=/runpod-volume/models
ENV H3_WORKFLOW_ROOT=/runpod-volume/workflows
ENV HF_HUB_ENABLE_HF_TRANSFER=1
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/opt/h3/scripts/entrypoint-h3.sh"]
