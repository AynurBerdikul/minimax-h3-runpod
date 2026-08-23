# Final rollout — existing RunPod infrastructure only

## OpenH3-IR preflight (before any paid render)

Set these environment variables on the existing endpoint/template; do not put them in workflow JSON or GitHub:

```text
H3IR_LLM_URL=<OpenAI-compatible URL ending in /v1>
H3IR_LLM_MODEL=<vision model id returned by /v1/models>
H3IR_LLM_API_KEY=<optional secret>
H3IR_TIMEOUT_SECONDS=180
H3IR_LOG_COMPILED_PROMPT=0
```

Run the non-rendering health check in the built container:

```bash
python /opt/h3/scripts/check_openh3ir.py
```

Do not submit `workflows/minimax_h3_i2v_openh3ir.api.json` unless all four lines say YES: installed, reachable/chat, model exists, and vision. A text-only model is not acceptable for reference-image tests.

The worker negotiates the two audited optional request hints (`seed`, `chat_template_kwargs`) from explicit provider 400/422 diagnostics. This supports providers such as Gemini without a provider-name switch. A log line about a one-time compatibility retry is expected; any other rejected field or ambiguous error remains a hard OpenH3 failure. There is no raw-prompt fallback.

For the first A/B test, stage exactly one source file as `openh3ir_test/input.png`. Run raw and compiled workflows with the same RunPod handler. Do not change seed, model, duration, resolution, steps, scheduler, VAE, encoder or sampler between them. Record measured results in `OPENH3IR_TEST_RESULTS.md`.

No new Pod, endpoint, volume, or GPU is created.

## Server repo changes

Merge/copy the files from this patch into the existing repository root, preserving paths.

One new GitHub Actions secret is required once:

- `RUNPOD_API_KEY` — a RunPod API key that can read the existing endpoint and update its bound template.

The existing S3 secrets remain unchanged.

After these files land on `main`, `.github/workflows/build-ghcr.yml`:

1. statically validates Python;
2. builds the existing image as an immutable `${GITHUB_SHA}` tag (plus `latest` for convenience);
3. updates the template already bound to endpoint `v4a23liws2f9jg` to that immutable tag;
4. RunPod performs the rolling release. Endpoint scaler/GPU/Network Volume settings are not recreated.

## Local Portable ComfyUI

Copy `client/ComfyUI-RunPod-H3` to:

`ComfyUI_windows_portable/ComfyUI/custom_nodes/ComfyUI-RunPod-H3`

Run `install_windows_portable.bat` once and restart ComfyUI.

Open RunPod H3 Settings and enter credentials once. The settings screen syncs the Network Volume model catalog. Use `RunPod Queue`, not the normal local Queue button, for remote-only model workflows.

## First H3 test

Use the current official workflow URL included in `OFFICIAL_H3_R2V_WORKFLOW.txt`.

Start with one reference image, short prompt, default/low official resolution and short duration. Keep the existing 24 GB GPU for this test. An OOM would be a hardware-capacity result; it does not require an architecture/storage redesign.
