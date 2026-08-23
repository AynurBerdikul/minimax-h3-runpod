# MiniMax H3 on RunPod Serverless — verified architecture

## Fixed design

Heavy model transfer never happens inside a paid GPU worker.

Data flow:

Hugging Face -> RunPod S3 API -> Network Volume -> Serverless ComfyUI -> GPU inference -> scale to zero

## Verified model set

Official source: `Comfy-Org/MiniMax-H3`

Files:
- FL2VA pruned INT8: 20,970,379,616 bytes
- REF2VA pruned INT8: 20,970,379,616 bytes
- Qwen3-VL-32B NVFP4/AWQ: 15,687,142,551 bytes
- Video VAE FP16: 5,207,808,496 bytes
- Audio VAE FP32: 605,254,808 bytes

Exact total: 63440965087 bytes = 59.08 GiB.

An 80 GB Network Volume is sufficient for this exact five-file set with substantial headroom.

## Version pins

- RunPod worker: `runpod/worker-comfyui:5.8.6-base`
- ComfyUI: merge commit `57500fc5bc92566a63f2046824f522cd55c335ca`
  from official MiniMax H3 PR #15224.

The H3 PR did not change `requirements.txt`, so the Dockerfile does not run a
broad `pip install` that could silently replace RunPod's CUDA/PyTorch stack.

## Network Volume

Serverless mounts the Network Volume at:

`/runpod-volume`

ComfyUI reads models from:

`/runpod-volume/models`

through `extra_model_paths.yaml`.

## Populate models BEFORE starting GPU

RunPod's S3-compatible API can access supported Network Volumes without launching a Pod or GPU.

EU-RO-1 is an S3-supported RunPod datacenter.

Install locally once:

```powershell
py -m pip install boto3 requests
```

Set environment variables:

```powershell
$env:RUNPOD_S3_ACCESS_KEY="..."
$env:RUNPOD_S3_SECRET_KEY="..."
$env:RUNPOD_VOLUME_ID="..."
$env:RUNPOD_S3_ENDPOINT="https://s3api-eu-ro-1.runpod.io/"
$env:RUNPOD_S3_REGION="EU-RO-1"
$env:ACCEPT_MINIMAX_H3_LICENSE="YES"
```

Then:

```powershell
py tools\populate_volume.py
py tools\verify_volume.py
```

`populate_volume.py`:
- never writes complete model files to the PC;
- streams in small RAM chunks;
- uses multipart S3 upload;
- resumes interrupted uploads;
- skips already-complete files;
- checks exact final size;
- stores the official expected SHA256 as S3 object metadata.

Only after `verify_volume.py` says `VOLUME READY` should any Serverless request be started.

## Endpoint settings

- Queue based
- Active workers: 0
- Max workers: 1
- GPU count: 1
- RTX 4090 24 GB currently selected
- Idle timeout: 5 sec
- FlashBoot: ON
- Network Volume attached
- Container disk: small/default; it is NOT used for model storage
- Container image: GHCR image built from this repo

## Failure policy

The worker performs no model downloads.

If the volume is missing or a model file has the wrong exact size, the worker
fails immediately before starting inference. This prevents long paid retries
that can never work.

## Reference-to-Video

Official ComfyUI native H3 support includes `MiniMaxH3ReferenceToVideo`.

The official R2V workflow supports:
- up to 9 reference images;
- up to 3 reference videos;
- up to 3 standalone reference audios;
- native synchronized video/audio output.

After the backend passes one R2V API generation, freeze that exact API-format
workflow and wrap it in the local ComfyUI cloud node.
