# OpenH3-IR A/B test results

Status: implementation prepared; render acceptance is blocked until a compatible vision LLM endpoint is configured and explicitly approved for a paid RunPod test.

## Fixed test inputs

- Raw prompt: `The two children keep running quickly through deep snow. Their movement is natural and energetic. The camera follows closely behind them at low height.`
- Same source image: `openh3ir_test/input.png`
- H3 checkpoint: `minimax_h3_fl2va_pruned_fp8_scaled.safetensors`
- Seed: `123`
- Duration: `5 seconds`
- FPS: `24`
- Canvas: `480 × 864` (`9:16`, `0.4 MP` ResolutionSelector budget)
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
| Prompt follows requested action | not rendered | not rendered |
| Character continuity | not rendered | not rendered |
| Camera continuity | not rendered | not rendered |
| Motion quality | not rendered | not rendered |
| Artifacts/morphing | not rendered | not rendered |
| Compile time | 0 | not measured |
| Render time | not measured | not measured |

## Performance

| Measurement | Before | After | Difference |
|---|---:|---:|---:|
| Docker image size | not recorded | not built | pending |
| Cold start | not recorded | not measured | pending |
| Peak container RAM | not recorded | not measured | pending |
| Peak VRAM | not recorded | not measured | pending |
| Network Volume | existing H3 models | unchanged | 0 bytes planned |

The compiler and custom node are installed in the Docker image, not on the Network Volume. No vision checkpoint, vLLM, Ollama, Transformers model cache, or other model weights are included.

CPU-only package validation on Python 3.12 installed `open-h3-ir 0.4.1`, ran `23 controls, 0 failing`, and confirmed the 5-second request snaps to 124 frames / 5.167 seconds. The disposable validation venv, including its own duplicated Python-side dependencies and scripts, occupied 46,897,968 bytes; this is not a Docker image-size measurement.

## Acceptance evidence to add

- `check_openh3ir.py` output with installed/reachable/model/chat/vision all YES.
- Compiled brief containing concrete visual details extracted from the shared reference image.
- Raw and compiled MP4 artifacts.
- `raw_prompt.txt`, `compiled_prompt.txt`, `generation_metadata.json` for both jobs.
- Worker cold-start time, compile time, render time, peak RAM and peak VRAM.
- Docker image sizes before and after.
