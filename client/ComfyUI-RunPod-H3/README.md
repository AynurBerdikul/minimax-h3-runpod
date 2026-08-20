# ComfyUI-RunPod-H3

Local ComfyUI stays the UI only. Inference runs on the existing RunPod Serverless endpoint and the 59+ GiB model weights stay on the RunPod Network Volume.

## One-time Windows Portable install

1. Copy this whole folder to:
   `ComfyUI_windows_portable/ComfyUI/custom_nodes/ComfyUI-RunPod-H3`
2. Run `install_windows_portable.bat` once.
3. Restart ComfyUI.
4. Open **RunPod H3 Settings** (gear next to **RunPod Queue**) and enter:
   - RunPod API key
   - Network Volume ID
   - Network Volume S3 access key / secret
   The existing endpoint ID and US-IL-1 S3 endpoint are prefilled.
5. Click **Save**. Saving also syncs the remote model catalog. Reload the ComfyUI page once if newly added model names do not appear immediately.

Secrets are stored only in this local plugin folder as `config.json`; the browser receives masked values.

## Why model names work without 59 GiB on the PC

The plugin registers zero-byte **catalog placeholders** for remote model names. They exist only so local ComfyUI dropdowns can compile a normal workflow. The real files are never copied to the PC and are loaded only by remote ComfyUI from `/runpod-volume/models`.

The five already-verified MiniMax H3 files are pre-registered in the catalog. **Do not use the normal local Queue button for remote-only workflows**: zero-byte placeholders are intentionally not valid local weights. Use **RunPod Queue**.

## Daily use

1. Open a compatible ComfyUI workflow.
2. Pick reference image/video/audio files from local ComfyUI input as usual and edit the prompt.
3. Click **RunPod Queue**.
4. The plugin compiles the graph to API format, sends small inputs inline and stages larger inputs on the Network Volume, then calls RunPod `/run` asynchronously.
5. RunPod wakes a Serverless GPU worker automatically. The worker runs backend ComfyUI against `/runpod-volume/models`.
6. Saved image/audio/video outputs are copied to `runpod-results/<job_id>/` on the Network Volume, downloaded to `ComfyUI/output/runpod/<job_id>/`, previewed in the result dialog, then deleted remotely after successful download by default.
7. The GPU worker scales back to zero according to the endpoint idle policy. No manual Start/Stop is required.

## Adding another model later

Upload it to the same Network Volume under a standard ComfyUI model directory (`models/loras`, `models/diffusion_models`, `models/vae`, etc.). Then open settings and click **Sync remote models**. Reload the ComfyUI page once so new dropdown choices are fetched.

## Adding another workflow later

No bridge changes are required as long as the remote Docker image contains every custom node used by that workflow and the required model files are present on the Network Volume.

## MiniMax H3 local ComfyUI version

The local ComfyUI must know the node schemas used by the workflow (including `MiniMaxH3ReferenceToVideo`). If a workflow opens with missing H3 core nodes, update that Portable ComfyUI once before the first test. This does not download the 59 GiB model weights.
