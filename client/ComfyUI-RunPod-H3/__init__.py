from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

import boto3
import requests
from aiohttp import web
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

import folder_paths
from server import PromptServer

WEB_DIRECTORY = "./web"
NODE_CLASS_MAPPINGS: dict[str, Any] = {}
NODE_DISPLAY_NAME_MAPPINGS: dict[str, str] = {}

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
MAX_INLINE = 4 * 1024 * 1024
RUNPOD_API = "https://api.runpod.ai/v2"
DEFAULTS = {
    "endpoint_id": "v4a23liws2f9jg",
    "runpod_api_key": "",
    "s3_endpoint": "https://s3api-us-il-1.runpod.io/",
    "s3_region": "US-IL-1",
    "volume_id": "",
    "s3_access_key": "",
    "s3_secret_key": "",
    "delete_remote_after_download": True,
}

# Best-effort cleanup map for large input objects staged before /run returns a job id.
# The remote worker also deletes each staging object after copying it, so cleanup is
# idempotent and safe if both sides attempt it.
_STAGED_BY_JOB: dict[str, list[str]] = {}

REMOTE_CATALOG_ROOT = ROOT / ".remote_models"
RAW_DIR_TO_CATEGORY = {
    "checkpoints": "checkpoints",
    "configs": "configs",
    "loras": "loras",
    "vae": "vae",
    "text_encoders": "text_encoders",
    "clip": "text_encoders",
    "diffusion_models": "diffusion_models",
    "unet": "diffusion_models",
    "clip_vision": "clip_vision",
    "style_models": "style_models",
    "embeddings": "embeddings",
    "diffusers": "diffusers",
    "vae_approx": "vae_approx",
    "controlnet": "controlnet",
    "t2i_adapter": "controlnet",
    "gligen": "gligen",
    "upscale_models": "upscale_models",
    "latent_upscale_models": "latent_upscale_models",
    "hypernetworks": "hypernetworks",
    "photomaker": "photomaker",
    "classifiers": "classifiers",
    "model_patches": "model_patches",
    "audio_encoders": "audio_encoders",
    "background_removal": "background_removal",
    "frame_interpolation": "frame_interpolation",
    "geometry_estimation": "geometry_estimation",
    "optical_flow": "optical_flow",
    "detection": "detection",
}

# These are already verified on the user's Network Volume. Tiny zero-byte catalog
# entries make remote-only models selectable in local ComfyUI without storing the
# real weights on the PC. They are never sent to the worker and never loaded locally.
BUILTIN_REMOTE_MODELS = {
    ("diffusion_models", "minimax_h3_fl2va_pruned_int8_convrot.safetensors"),
    ("diffusion_models", "minimax_h3_ref2va_pruned_int8_convrot.safetensors"),
    ("text_encoders", "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"),
    ("vae", "minimax_h3_video_vae_fp16.safetensors"),
    ("vae", "minimax_h3_audio_vae_fp32.safetensors"),
}


def _invalidate_model_caches(categories: set[str]) -> None:
    cache = getattr(folder_paths, "filename_list_cache", None)
    if isinstance(cache, dict):
        for category in categories:
            cache.pop(category, None)
    helper = getattr(folder_paths, "cache_helper", None)
    if helper is not None and hasattr(helper, "clear"):
        try:
            helper.clear()
        except Exception:
            pass


def _safe_catalog_rel(value: str) -> Path | None:
    raw = value.replace("\\", "/").strip("/")
    p = PurePosixPath(raw)
    if not p.parts or p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        return None
    return Path(*p.parts)


def _register_remote_catalog_dirs() -> None:
    categories = set(RAW_DIR_TO_CATEGORY.values())
    for category in sorted(categories):
        directory = (REMOTE_CATALOG_ROOT / category).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        folder_paths.add_model_folder_path(category, str(directory), is_default=False)
    _write_catalog(BUILTIN_REMOTE_MODELS, replace=False)


def _write_catalog(entries: set[tuple[str, str]], *, replace: bool) -> dict[str, Any]:
    desired: dict[str, set[Path]] = {}
    for category, rel_name in entries:
        if category not in set(RAW_DIR_TO_CATEGORY.values()):
            continue
        rel = _safe_catalog_rel(rel_name)
        if rel is None:
            continue
        desired.setdefault(category, set()).add(rel)

    # Built-in known models must survive every remote sync even if an S3 listing is
    # temporarily unavailable or eventually-consistent.
    for category, rel_name in BUILTIN_REMOTE_MODELS:
        rel = _safe_catalog_rel(rel_name)
        if rel is not None:
            desired.setdefault(category, set()).add(rel)

    touched_categories: set[str] = set()
    for category in set(RAW_DIR_TO_CATEGORY.values()):
        root = (REMOTE_CATALOG_ROOT / category).resolve()
        root.mkdir(parents=True, exist_ok=True)
        wanted = desired.get(category, set())
        if replace:
            for existing in sorted(root.rglob("*"), reverse=True):
                try:
                    if existing.is_file():
                        rel = existing.relative_to(root)
                        if rel not in wanted:
                            existing.unlink()
                            touched_categories.add(category)
                    elif existing.is_dir() and not any(existing.iterdir()):
                        existing.rmdir()
                except OSError:
                    pass
        for rel in wanted:
            target = (root / rel).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.touch()
                touched_categories.add(category)

    if touched_categories:
        _invalidate_model_caches(touched_categories)
    return {
        "count": sum(len(v) for v in desired.values()),
        "categories": {k: len(v) for k, v in sorted(desired.items()) if v},
    }


def _storage_configured(cfg: dict[str, Any]) -> bool:
    return all(cfg.get(k) for k in (
        "s3_endpoint", "s3_region", "volume_id", "s3_access_key", "s3_secret_key"
    ))


def _list_remote_model_entries(cfg: dict[str, Any]) -> set[tuple[str, str]]:
    if not _storage_configured(cfg):
        raise RuntimeError("Network Volume S3 settings are incomplete")
    s3 = _s3(cfg)
    entries: set[tuple[str, str]] = set()
    continuation = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": cfg["volume_id"], "Prefix": "models/", "MaxKeys": 1000}
        if continuation:
            kwargs["ContinuationToken"] = continuation
        response = s3.list_objects_v2(**kwargs)
        for item in response.get("Contents", []) or []:
            key = str(item.get("Key") or "").replace("\\", "/").lstrip("/")
            if not key.startswith("models/"):
                continue
            rest = key[len("models/"):]
            parts = PurePosixPath(rest).parts
            if len(parts) < 2:
                continue
            category = RAW_DIR_TO_CATEGORY.get(parts[0])
            if not category:
                continue
            rel_name = PurePosixPath(*parts[1:]).as_posix()
            if _safe_catalog_rel(rel_name) is not None:
                entries.add((category, rel_name))
        if not response.get("IsTruncated"):
            break
        continuation = response.get("NextContinuationToken")
        if not continuation:
            raise RuntimeError("Truncated S3 model listing without continuation token")
    return entries


def _sync_remote_models(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or _load_config()
    entries = _list_remote_model_entries(cfg)
    result = _write_catalog(entries, replace=True)
    result["remote_objects"] = len(entries)
    return result


# Register virtual model folders before ComfyUI builds /object_info. This costs
# almost no disk space and makes the already-verified H3 filenames usable in the UI.
_register_remote_catalog_dirs()


def _load_config() -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.is_file():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except Exception:
            pass
    # Environment variables are useful for managed/local deployments and never get written back.
    env_map = {
        "endpoint_id": "RUNPOD_ENDPOINT_ID",
        "runpod_api_key": "RUNPOD_API_KEY",
        "s3_endpoint": "RUNPOD_S3_ENDPOINT",
        "s3_region": "RUNPOD_S3_REGION",
        "volume_id": "RUNPOD_VOLUME_ID",
        "s3_access_key": "RUNPOD_S3_ACCESS_KEY",
        "s3_secret_key": "RUNPOD_S3_SECRET_KEY",
    }
    for key, env in env_map.items():
        if os.getenv(env):
            cfg[key] = os.environ[env]
    return cfg


def _masked(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••••••"
    return value[:4] + "••••••••" + value[-4:]


def _save_config(update: dict[str, Any]) -> dict[str, Any]:
    allowed = set(DEFAULTS)
    current = dict(DEFAULTS)
    if CONFIG_PATH.is_file():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                current.update({k: v for k, v in raw.items() if k in allowed})
        except Exception:
            pass
    for key, value in update.items():
        if key not in allowed:
            continue
        if key in {"runpod_api_key", "s3_access_key", "s3_secret_key"} and value in (None, ""):
            # Empty secret in the UI means "keep existing".
            continue
        current[key] = value
    CONFIG_PATH.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    return _load_config()


def _require_config(cfg: dict[str, Any]) -> None:
    missing = [
        key for key in (
            "endpoint_id",
            "runpod_api_key",
            "s3_endpoint",
            "s3_region",
            "volume_id",
            "s3_access_key",
            "s3_secret_key",
        ) if not cfg.get(key)
    ]
    if missing:
        raise RuntimeError("RunPod Remote settings are incomplete: " + ", ".join(missing))


def _s3(cfg: dict[str, Any]):
    return boto3.client(
        "s3",
        endpoint_url=cfg["s3_endpoint"],
        region_name=cfg["s3_region"],
        aws_access_key_id=cfg["s3_access_key"],
        aws_secret_access_key=cfg["s3_secret_key"],
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 8, "mode": "adaptive"},
        ),
    )


def _runpod_headers(cfg: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {cfg['runpod_api_key']}", "Content-Type": "application/json"}


def _normalize_input_name(value: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.split(" [", 1)[0].replace("\\", "/").strip()
    p = PurePosixPath(raw)
    if p.is_absolute() or not p.parts or any(part in ("", ".", "..") for part in p.parts):
        return None
    return Path(*p.parts).as_posix()


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)


def _find_local_inputs(workflow: dict[str, Any]) -> list[tuple[str, Path]]:
    input_root = Path(folder_paths.get_input_directory()).resolve()
    found: dict[str, Path] = {}
    for value in _walk_strings(workflow):
        name = _normalize_input_name(value)
        if not name:
            continue
        candidate = (input_root / Path(name)).resolve()
        try:
            candidate.relative_to(input_root)
        except ValueError:
            continue
        if candidate.is_file():
            found[name] = candidate
    return sorted(found.items())


def _submit_sync(workflow: dict[str, Any]) -> dict[str, Any]:
    cfg = _load_config()
    _require_config(cfg)
    s3 = _s3(cfg)
    inline_files: list[dict[str, str]] = []
    volume_files: list[dict[str, str]] = []
    stage_token = uuid.uuid4().hex

    for name, path in _find_local_inputs(workflow):
        size = path.stat().st_size
        if size <= MAX_INLINE:
            inline_files.append({"name": name, "data": base64.b64encode(path.read_bytes()).decode("ascii")})
        else:
            key = f"runpod-inputs/{stage_token}/{name}"
            s3.upload_file(str(path), cfg["volume_id"], key)
            volume_files.append({"name": name, "key": key})

    payload = {
        "input": {"workflow": workflow},
        # H3 video generation on a 24 GB card can legitimately exceed RunPod's
        # 10-minute endpoint default. A per-job policy avoids changing the
        # endpoint or paying for an always-on worker.
        "policy": {"executionTimeout": 3_600_000, "ttl": 7_200_000},
    }
    if inline_files:
        payload["input"]["files"] = inline_files
    if volume_files:
        payload["input"]["volume_files"] = volume_files

    url = f"{RUNPOD_API}/{cfg['endpoint_id']}/run"
    try:
        r = requests.post(url, headers=_runpod_headers(cfg), json=payload, timeout=60)
        if not r.ok:
            raise RuntimeError(f"RunPod /run failed: HTTP {r.status_code}: {r.text[:4000]}")
        data = r.json()
        job_id = data.get("id")
        if not job_id:
            raise RuntimeError(f"RunPod /run returned no job id: {data}")
        job_id = str(job_id)
        if volume_files:
            _STAGED_BY_JOB[job_id] = [str(item["key"]) for item in volume_files]
        return {"job_id": job_id, "status": data.get("status", "IN_QUEUE"), "staged": len(volume_files)}
    except Exception:
        # If submission itself fails, the remote worker will never clean staged inputs.
        for item in volume_files:
            try:
                s3.delete_object(Bucket=cfg["volume_id"], Key=item["key"])
            except Exception:
                pass
        raise


def _cleanup_staged_for_job(cfg: dict[str, Any], job_id: str) -> None:
    keys = _STAGED_BY_JOB.pop(job_id, [])
    if not keys:
        return
    try:
        s3 = _s3(cfg)
    except Exception:
        return
    for key in keys:
        try:
            s3.delete_object(Bucket=cfg["volume_id"], Key=key)
        except Exception:
            pass


def _wait_for_s3_object(s3, bucket: str, key: str, expected: int | None) -> int:
    last = None
    for _ in range(60):
        try:
            h = s3.head_object(Bucket=bucket, Key=key)
            size = int(h.get("ContentLength", -1))
            if size > 0 and (not expected or size == expected):
                return size
            last = f"size {size}, expected {expected}"
        except ClientError as exc:
            last = str(exc)
        time.sleep(1)
    raise RuntimeError(f"Result object not ready on Network Volume: {key}: {last}")


def _materialize_outputs(cfg: dict[str, Any], job_id: str, output: dict[str, Any]) -> list[dict[str, Any]]:
    if output.get("status") == "error":
        raise RuntimeError(output.get("error") or "remote worker failed")
    artifacts = output.get("artifacts") or []
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeError(f"RunPod completed but returned no artifacts: {output}")

    s3 = _s3(cfg)
    out_root = Path(folder_paths.get_output_directory()).resolve()
    job_dir = (out_root / "runpod" / job_id).resolve()
    job_dir.mkdir(parents=True, exist_ok=True)
    local: list[dict[str, Any]] = []

    for index, item in enumerate(artifacts, 1):
        if not isinstance(item, dict) or not item.get("key"):
            raise RuntimeError(f"invalid artifact descriptor: {item}")
        key = str(item["key"]).replace("\\", "/").lstrip("/")
        if not key.startswith("runpod-results/"):
            raise RuntimeError(f"refusing to download unexpected volume key: {key}")
        expected = int(item.get("size") or 0) or None

        filename = Path(str(item.get("filename") or f"artifact_{index}")).name
        if not filename:
            filename = f"artifact_{index}"
        dest = (job_dir / filename).resolve()
        try:
            dest.relative_to(job_dir)
        except ValueError as exc:
            raise RuntimeError("unsafe output filename") from exc

        local_ready = dest.is_file() and dest.stat().st_size > 0 and (not expected or dest.stat().st_size == expected)
        if not local_ready:
            _wait_for_s3_object(s3, cfg["volume_id"], key, expected)
            tmp = dest.with_suffix(dest.suffix + ".part")
            with tmp.open("wb") as f:
                s3.download_fileobj(cfg["volume_id"], key, f)
            if expected and tmp.stat().st_size != expected:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"downloaded size mismatch for {filename}")
            if tmp.stat().st_size <= 0:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"downloaded empty output for {filename}")
            tmp.replace(dest)

        rel = dest.relative_to(out_root).as_posix()
        local.append(
            {
                "filename": dest.name,
                "subfolder": str(Path(rel).parent).replace("\\", "/") if "/" in rel else "",
                "type": "output",
                "content_type": item.get("content_type") or "application/octet-stream",
                "size": dest.stat().st_size,
            }
        )
        if cfg.get("delete_remote_after_download", True):
            try:
                s3.delete_object(Bucket=cfg["volume_id"], Key=key)
            except Exception:
                pass
    return local


def _status_sync(job_id: str) -> dict[str, Any]:
    cfg = _load_config()
    _require_config(cfg)
    url = f"{RUNPOD_API}/{cfg['endpoint_id']}/status/{job_id}"
    r = requests.get(url, headers=_runpod_headers(cfg), timeout=30)
    if not r.ok:
        raise RuntimeError(f"RunPod status failed: HTTP {r.status_code}: {r.text[:2000]}")
    data = r.json()
    state = data.get("status", "UNKNOWN")
    result: dict[str, Any] = {
        "job_id": job_id,
        "status": state,
        "delay_time": data.get("delayTime"),
        "execution_time": data.get("executionTime"),
    }
    if state == "COMPLETED":
        output = data.get("output") or {}
        result["outputs"] = _materialize_outputs(cfg, job_id, output)
        result["remote"] = output
        _cleanup_staged_for_job(cfg, job_id)
    elif state in {"FAILED", "CANCELLED", "TIMED_OUT"}:
        result["error"] = data.get("error") or data.get("output") or state
        _cleanup_staged_for_job(cfg, job_id)
    return result


def _cancel_sync(job_id: str) -> dict[str, Any]:
    cfg = _load_config()
    _require_config(cfg)
    url = f"{RUNPOD_API}/{cfg['endpoint_id']}/cancel/{job_id}"
    r = requests.post(url, headers=_runpod_headers(cfg), timeout=30)
    if not r.ok:
        raise RuntimeError(f"RunPod cancel failed: HTTP {r.status_code}: {r.text[:2000]}")
    _cleanup_staged_for_job(cfg, job_id)
    return r.json() if r.text else {"status": "CANCELLED"}


def _background_model_sync() -> None:
    try:
        cfg = _load_config()
        if _storage_configured(cfg):
            _sync_remote_models(cfg)
    except Exception:
        # Offline startup must remain usable; the manual Sync action exposes errors.
        pass


threading.Thread(target=_background_model_sync, name="runpod-h3-model-sync", daemon=True).start()


routes = PromptServer.instance.routes


@routes.get("/runpod-h3/config")
async def get_config(_request):
    cfg = _load_config()
    safe = dict(cfg)
    safe["runpod_api_key"] = _masked(str(cfg.get("runpod_api_key") or ""))
    safe["s3_access_key"] = _masked(str(cfg.get("s3_access_key") or ""))
    safe["s3_secret_key"] = _masked(str(cfg.get("s3_secret_key") or ""))
    safe["configured"] = all(cfg.get(k) for k in (
        "endpoint_id", "runpod_api_key", "s3_endpoint", "s3_region", "volume_id", "s3_access_key", "s3_secret_key"
    ))
    return web.json_response(safe)


@routes.post("/runpod-h3/config")
async def set_config(request):
    try:
        body = await request.json()
        cfg = _save_config(body if isinstance(body, dict) else {})
        configured = all(cfg.get(k) for k in (
            "endpoint_id", "runpod_api_key", "s3_endpoint", "s3_region", "volume_id", "s3_access_key", "s3_secret_key"
        ))
        response: dict[str, Any] = {"ok": True, "configured": configured}
        if _storage_configured(cfg):
            try:
                response["model_sync"] = await asyncio.to_thread(_sync_remote_models, cfg)
            except Exception as sync_exc:
                response["model_sync_error"] = f"{type(sync_exc).__name__}: {sync_exc}"
        return web.json_response(response)
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)


@routes.post("/runpod-h3/sync-models")
async def sync_models(_request):
    try:
        data = await asyncio.to_thread(_sync_remote_models, _load_config())
        return web.json_response({"ok": True, **data})
    except Exception as exc:
        return web.json_response({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=400)


@routes.post("/runpod-h3/submit")
async def submit(request):
    try:
        body = await request.json()
        workflow = body.get("workflow") if isinstance(body, dict) else None
        if not isinstance(workflow, dict) or not workflow:
            raise ValueError("workflow must be a non-empty ComfyUI API-format object")
        data = await asyncio.to_thread(_submit_sync, workflow)
        return web.json_response(data)
    except Exception as exc:
        return web.json_response({"error": f"{type(exc).__name__}: {exc}"}, status=400)


@routes.get("/runpod-h3/status/{job_id}")
async def status(request):
    try:
        data = await asyncio.to_thread(_status_sync, request.match_info["job_id"])
        return web.json_response(data)
    except Exception as exc:
        return web.json_response({"error": f"{type(exc).__name__}: {exc}"}, status=500)


@routes.post("/runpod-h3/cancel/{job_id}")
async def cancel(request):
    try:
        data = await asyncio.to_thread(_cancel_sync, request.match_info["job_id"])
        return web.json_response(data)
    except Exception as exc:
        return web.json_response({"error": f"{type(exc).__name__}: {exc}"}, status=500)
