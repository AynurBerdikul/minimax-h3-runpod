# MiniMax H3 on RunPod Serverless — verified architecture

## Optional OpenH3-IR I2V compiler

The raw H3 path remains unchanged. A second API-format workflow is available for an A/B test:

- `workflows/minimax_h3_i2v_raw.api.json`
- `workflows/minimax_h3_i2v_openh3ir.api.json`

The image, raw prompt, H3 FP8 checkpoint, sampler seed, duration, resolution, scheduler and 20 sampling steps are identical. The B arm changes only the prompt pipeline. It uses the official pinned [ComfyUI-OpenH3-IR](https://github.com/ruashots/ComfyUI-OpenH3-IR) node pack and [open-h3-ir](https://github.com/ruashots/open-h3-ir) compiler.

OpenH3-IR does not load a vision model in this worker. Configure a separate OpenAI-compatible vision endpoint at runtime:

```text
H3IR_LLM_URL=https://vision-endpoint.example/v1
H3IR_LLM_MODEL=vision-model-id
H3IR_LLM_API_KEY=optional-secret
H3IR_TIMEOUT_SECONDS=180
H3IR_LOG_COMPILED_PROMPT=0
```

`H3IR_LLM_API_KEY` is mapped in memory to upstream's `H3IR_LLM_KEY`; it is never printed or written to an artifact. `H3IR_ALLOW_DEGRADED=0` is explicit, so an OpenH3 compilation failure cannot silently fall back to the raw prompt.

### OpenAI-compatible provider negotiation

The pinned compiler was built against vLLM and normally sends the optional top-level request fields `seed` and `chat_template_kwargs`. Some otherwise compatible providers reject one or both fields. The image applies the pinned patch `patches/openh3ir-provider-compat.patch`, which is intentionally provider-neutral:

- it first sends the normal upstream request;
- only after a 400/422 response explicitly identifies an audited optional field as unknown or unsupported, it retries that same LLM request once without the named field;
- it caches that capability for the remaining compiler stages;
- it never removes `model`, `messages`, `max_tokens`, media, or output validation;
- ambiguous errors still fail, and failure never falls back to a raw H3 prompt.

The Docker build uses `git apply --check`, so an upstream pin change that no longer matches the patch stops the build instead of silently producing a different integration. `scripts/test_openh3ir_provider_compat.py` verifies negotiation, caching, and loud failure for ambiguous or required-field errors.

The ordinary worker boot does not require these variables. Raw H3 workflows continue to run when they are absent. An OpenH3 workflow fails clearly at its compiler node.

Before the first OpenH3 render, run inside the image:

```bash
python /opt/h3/scripts/check_openh3ir.py
```

Success requires `health=True`, `chat_ok=True`, the selected model in `model_ids`, and `vision_ok=True`; the wrapper does not trust the upstream doctor's exit code.

The B workflow uses the upstream node's real API contract. `OpenH3IRCompile` returns the loaded model, conditioning, latent, VAEs and compiled prompt; upstream does not expose a compiler-only STRING node that can be placed immediately before `MiniMaxH3ImageToVideo`. Writing a local replacement node would fork upstream behavior, so the official outputs feed the existing sampler/decode/save tail instead.

The disconnected `LoadImage` node in the API workflow is a deliberate transport marker. The RunPod handler discovers files from ordinary workflow inputs, while the OpenH3 media tray stores its filename inside JSON text. The marker causes the existing handler to stage the same file and is not connected to the execution graph; OpenH3 and H3 still read the single identical source asset.

Tagged `PreviewAny` outputs make the handler save `raw_prompt.txt`, `compiled_prompt.txt`, `compile_report.txt`, and `generation_metadata.json` beside the MP4. The raw control saves the raw prompt as both raw and compiled text. No credentials are accepted in metadata.

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
