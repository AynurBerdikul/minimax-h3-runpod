# Final rollout — existing RunPod infrastructure only

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
