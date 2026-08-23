# OpenH3-IR A/B test results

Status: OpenH3-IR live smoke render passed on the existing endpoint. A controlled raw-vs-compiled A/B is still pending; the successful compiled run must not be presented as an A/B result.

## Fixed test inputs

- Raw prompt: `The two children keep running quickly through deep snow. Their movement is natural and energetic. The camera follows closely behind them at low height.`
- Same source image: `openh3ir_test/input.png`
- H3 checkpoint: `minimax_h3_fl2va_pruned_fp8_scaled.safetensors`
- Seed: `123`
- Duration: `5 seconds`
- FPS: `24`
- Requested canvas budget: `9:16`, `0.4 MP`
- Actual OpenH3 canvas: `480 × 832` (the pinned compiler's frame-grid result)
- Steps: `20`
- Scheduler: `simple`
- Turbo LoRA: off
- OpenH3 creativity: `balanced`
- Director: none

## Comparison

| Test | Raw H3 | OpenH3-IR |
|---|---:|---:|
| Same seed | yes | yes |
| Same model | yes | yes |
| Same image | yes | yes |
| Same duration | yes | yes |
| Same resolution | yes | yes |
| Same steps/scheduler | yes | yes |
| Prompt follows requested action | not rendered | generated; visual review pending |
| Character continuity | not rendered | visual review pending |
| Camera continuity | not rendered | visual review pending |
| Motion quality | not rendered | visual review pending |
| Artifacts/morphing | not rendered | visual review pending |
| Compile time | 0 | about 40 s to first H3 model-load log (inferred, not a dedicated timer) |
| End-to-end execution | not measured | 305.222 s |

## Performance

| Measurement | Before | After | Difference |
|---|---:|---:|---:|
| Docker image size | not recorded | not built | pending |
| Cold start / queue delay | not recorded | 58.842 s | comparison pending |
| Peak container RAM | not recorded | not measured | pending |
| Peak VRAM | not recorded | not measured | pending |
| Network Volume | existing H3 models | unchanged | 0 bytes planned |

The compiler and custom node are installed in the Docker image, not on the Network Volume. No vision checkpoint, vLLM, Ollama, Transformers model cache, or other model weights are included.

CPU-only package validation on Python 3.12 installed `open-h3-ir 0.4.1`, ran `23 controls, 0 failing`, and confirmed the 5-second request snaps to 124 frames / 5.167 seconds. The disposable validation venv, including its own duplicated Python-side dependencies and scripts, occupied 46,897,968 bytes; this is not a Docker image-size measurement.

## Live OpenH3 smoke run — 2026-08-24

- Existing endpoint: `2qdqfqf67tm4y5`, release 12.
- Immutable image: `ghcr.io/aynurberdikul/minimax-h3-runpod:b3c402024a22383a7665b29b45d81d55d48d9412`.
- RunPod job: `4bf02fed-d4c2-4766-a860-40fda17e12e9-e1`.
- Result: `COMPLETED`; queue/cold-start delay 58.842 s; execution 305.222 s.
- MP4: H.264 + AAC, 480 × 832, 24 fps, 5.167 s, 1,323,930 bytes.
- Vision evidence: the compiled brief identifies two girls, their distinct white-blonde/light-brown hair, matching dark fur-trimmed winter clothing, deep snow, pine forest, reddish-pink sun, the finger-gun gesture, and frontal backward tracking from the single shared reference image.
- Saved artifacts: MP4, `raw_prompt.txt`, `compiled_prompt.txt`, `compile_report.txt`, and `generation_metadata.json`.
- Provider compatibility evidence: Gemini first rejected the optional upstream fields `seed` and `chat_template_kwargs`; the provider-neutral adapter removed only the explicitly rejected audited fields, retried once, cached that capability, and received HTTP 200. H3 sampling then started normally.

Observed compiler defect: the raw request said `non_diegetic_music: none`, but the compiled brief invented an orchestral score. This is recorded as a failed negative-constraint preservation case; it is not silently labelled a quality improvement. The requested 480 × 864 label also does not describe the compiler's actual 480 × 832 frame-grid output. Both must be resolved before a strict A/B acceptance run.

## Acceptance evidence to add

- A captured `check_openh3ir.py` report with installed/reachable/model/chat/vision all YES (the live render already demonstrated chat and reference-image vision end to end).
- Raw and compiled MP4 artifacts.
- Matching `raw_prompt.txt`, `compiled_prompt.txt`, `generation_metadata.json` for the raw control job.
- Worker cold-start time, compile time, render time, peak RAM and peak VRAM.
- Docker image sizes before and after.
