# MiniMax H3 — ComfyUI + RunPod Serverless

Ready-to-upload baseline for open-weight MiniMax H3 on RunPod Serverless.

## Architecture

- RunPod `worker-comfyui:5.8.6-base`
- current ComfyUI checked out at image build so native H3 nodes exist
- H3 model files stored on a RunPod Network Volume, not in Docker
- FL2VA + REF2VA included
- official Comfy-Org T2V / I2V / R2V templates
- async `/run` test client
- GitHub Actions -> GHCR

## Important

H3 support is new. The first RTX 3090 / 24 GB run is an acceptance test, not a guaranteed benchmark. There are active upstream reports around H3 on 24 GB cards.

Avoid SageAttention for H3 unless upstream explicitly recommends it.

## Model files

From `Comfy-Org/MiniMax-H3`:

```text
diffusion_models/
  minimax_h3_fl2va_pruned_int8_convrot.safetensors
  minimax_h3_ref2va_pruned_int8_convrot.safetensors
text_encoders/
  qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
vae/
  minimax_h3_video_vae_fp16.safetensors
  minimax_h3_audio_vae_fp32.safetensors
```

## 1. Upload to GitHub

Upload the contents of this folder to:

`AynurBerdikul/minimax-h3-runpod`

Commit to `main`.

GitHub Actions builds:

`ghcr.io/aynurberdikul/minimax-h3-runpod:latest`

and a SHA tag. After a successful test, use the SHA tag in RunPod.

## 2. RunPod Network Volume

Create a Network Volume, initially **100–120 GB**.

Attach it to the Serverless endpoint. The worker expects:

`/runpod-volume`

## 3. Endpoint settings

Keep:

```text
Queue based
Active workers: 0
Max workers: 1
GPU count: 1
Idle timeout: 5 sec
FlashBoot: ON
Auto scaling: Queue delay
```

Attach the Network Volume and change Container Image to:

`ghcr.io/aynurberdikul/minimax-h3-runpod:latest`

## 4. Environment variables

Set:

`ACCEPT_MINIMAX_H3_LICENSE=YES`

only after reviewing the MiniMax H3 license.

Optional:

`HF_TOKEN=...`

Never commit secrets.

## 5. First boot

If model files are missing, the worker downloads them once to the Network Volume. Later starts reuse them.

The first start is slower and billed while the worker is alive. This package avoids repeating that download.

## 6. Official workflows

Current official UI templates are downloaded to:

`/runpod-volume/workflows`

RunPod `worker-comfyui` expects API-format workflow JSON. For the first R2V test:
1. Open the official R2V template in current local ComfyUI.
2. `Workflow -> Export (API)`.
3. Save the exported API workflow locally.
4. Send it with `client/runpod_submit.py`.

## 7. Async test

Set:

`RUNPOD_API_KEY`
`RUNPOD_ENDPOINT_ID`

Then run:

```bash
python client/runpod_submit.py --workflow h3_r2v_api.json   --image ref1.png=/path/to/ref1.png   --image ref2.png=/path/to/ref2.png
```

## Why there is no fake local cloud-node yet

A local `H3 Cloud Reference` ComfyUI node should be created only after one exact R2V API workflow succeeds. Then the working node IDs/schema can be frozen and wrapped safely.

That is the only remaining integration step after the endpoint itself works.
