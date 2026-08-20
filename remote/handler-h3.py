from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shutil
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

import requests
import runpod

COMFY_URL = os.getenv("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
COMFY_INPUT = Path(os.getenv("COMFY_INPUT_DIR", "/comfyui/input")).resolve()
COMFY_OUTPUT = Path(os.getenv("COMFY_OUTPUT_DIR", "/comfyui/output")).resolve()
COMFY_TEMP = Path(os.getenv("COMFY_TEMP_DIR", "/comfyui/temp")).resolve()
VOLUME_ROOT = Path(os.getenv("RUNPOD_VOLUME_ROOT", "/runpod-volume")).resolve()
RESULT_ROOT = (VOLUME_ROOT / "runpod-results").resolve()
INPUT_ROOT = (VOLUME_ROOT / "runpod-inputs").resolve()
MAX_INLINE_BYTES = int(os.getenv("H3_MAX_INLINE_INPUT_BYTES", str(16 * 1024 * 1024)))
COMFY_START_TIMEOUT = int(os.getenv("H3_COMFY_START_TIMEOUT", "180"))
POLL_SECONDS = float(os.getenv("H3_HISTORY_POLL_SECONDS", "1.0"))
JOB_TIMEOUT = int(os.getenv("H3_JOB_TIMEOUT_SECONDS", "3300"))
STALE_TTL_SECONDS = int(os.getenv("H3_STALE_ARTIFACT_TTL_SECONDS", str(24 * 60 * 60)))


def _inside(base: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _safe_relpath(value: str) -> Path:
    """Return a normalized relative path; reject absolute/traversal paths."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("file name must be a non-empty string")
    raw = value.split(" [", 1)[0].replace("\\", "/").strip()
    p = PurePosixPath(raw)
    if p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        raise ValueError(f"unsafe relative path: {value!r}")
    return Path(*p.parts)


def _safe_volume_key(value: str, required_prefix: str) -> Path:
    rel = _safe_relpath(value)
    if not rel.parts or rel.parts[0] != required_prefix:
        raise ValueError(f"volume key must be under {required_prefix}/")
    path = (VOLUME_ROOT / rel).resolve()
    if not _inside(VOLUME_ROOT, path):
        raise ValueError("volume key escapes mounted volume")
    return path


def _wait_for_comfy() -> None:
    deadline = time.time() + COMFY_START_TIMEOUT
    last_error = ""
    while time.time() < deadline:
        try:
            r = requests.get(f"{COMFY_URL}/system_stats", timeout=5)
            if r.ok:
                return
            last_error = f"HTTP {r.status_code}"
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"ComfyUI did not become ready within {COMFY_START_TIMEOUT}s: {last_error}")


def _decode_inline(data: str) -> bytes:
    if not isinstance(data, str):
        raise ValueError("inline file data must be base64 text")
    if data.startswith("data:"):
        try:
            data = data.split(",", 1)[1]
        except IndexError as exc:
            raise ValueError("invalid data URL") from exc
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception as exc:
        raise ValueError("invalid base64 input") from exc
    if len(raw) > MAX_INLINE_BYTES:
        raise ValueError(
            f"inline input is {len(raw)} bytes; limit is {MAX_INLINE_BYTES}. "
            "Stage larger files on the Network Volume instead."
        )
    return raw


def _stage_inputs(payload: dict[str, Any]) -> list[Path]:
    """Stage local/volume inputs into ComfyUI input and return temp paths to remove."""
    created: list[Path] = []
    COMFY_INPUT.mkdir(parents=True, exist_ok=True)

    for item in payload.get("files") or []:
        if not isinstance(item, dict):
            raise ValueError("files entries must be objects")
        rel = _safe_relpath(item.get("name", ""))
        dest = (COMFY_INPUT / rel).resolve()
        if not _inside(COMFY_INPUT, dest):
            raise ValueError("input path escapes ComfyUI input directory")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_decode_inline(item.get("data", "")))
        created.append(dest)

    for item in payload.get("volume_files") or []:
        if not isinstance(item, dict):
            raise ValueError("volume_files entries must be objects")
        rel = _safe_relpath(item.get("name", ""))
        src = _safe_volume_key(item.get("key", ""), "runpod-inputs")
        if not src.is_file():
            raise FileNotFoundError(f"staged input not found on Network Volume: {item.get('key')}")
        dest = (COMFY_INPUT / rel).resolve()
        if not _inside(COMFY_INPUT, dest):
            raise ValueError("input path escapes ComfyUI input directory")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        # The staged Network Volume object is only a transport object. Once it is
        # safely copied into this worker's input directory, remove it so cancelled/
        # repeated generations cannot slowly fill persistent storage.
        try:
            src.unlink(missing_ok=True)
        except OSError:
            pass
        created.append(dest)

    return created


def _queue_prompt(workflow: dict[str, Any], client_id: str) -> str:
    r = requests.post(
        f"{COMFY_URL}/prompt",
        json={"prompt": workflow, "client_id": client_id},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"ComfyUI rejected workflow: HTTP {r.status_code}: {r.text[:4000]}")
    data = r.json()
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI /prompt response has no prompt_id: {data}")
    return str(prompt_id)


def _history(prompt_id: str) -> dict[str, Any] | None:
    r = requests.get(f"{COMFY_URL}/history/{prompt_id}", timeout=15)
    r.raise_for_status()
    data = r.json()
    if prompt_id in data:
        return data[prompt_id]
    # Some ComfyUI builds return the history object directly.
    if data.get("prompt") or data.get("outputs") or data.get("status"):
        return data
    return None


def _execution_error(history: dict[str, Any]) -> str | None:
    status = history.get("status") or {}
    if status.get("status_str") == "error":
        return json.dumps(status, ensure_ascii=False)[:8000]
    for msg in status.get("messages") or []:
        if isinstance(msg, (list, tuple)) and msg and msg[0] == "execution_error":
            return json.dumps(msg[1] if len(msg) > 1 else msg, ensure_ascii=False)[:8000]
    return None


def _wait_for_history(prompt_id: str) -> dict[str, Any]:
    deadline = time.time() + JOB_TIMEOUT
    while time.time() < deadline:
        history = _history(prompt_id)
        if history:
            err = _execution_error(history)
            if err:
                raise RuntimeError(f"ComfyUI execution failed: {err}")
            status = history.get("status") or {}
            completed = status.get("completed") is True or status.get("status_str") == "success"
            # Completed histories have outputs even on builds that omit status.completed.
            if completed or (history.get("outputs") and status.get("status_str") not in ("running", "pending")):
                return history
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"ComfyUI job exceeded internal {JOB_TIMEOUT}s timeout")


def _collect_saved_results(value: Any, out: list[dict[str, str]]) -> None:
    if isinstance(value, dict):
        filename = value.get("filename")
        if isinstance(filename, str) and filename:
            folder_type = value.get("type", "output")
            if folder_type in ("output", "temp"):
                out.append(
                    {
                        "filename": filename,
                        "subfolder": str(value.get("subfolder") or ""),
                        "type": folder_type,
                    }
                )
        for child in value.values():
            _collect_saved_results(child, out)
    elif isinstance(value, list):
        for child in value:
            _collect_saved_results(child, out)


def _saved_results(history: dict[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    _collect_saved_results(history.get("outputs") or {}, found)
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in found:
        key = (item["filename"], item["subfolder"], item["type"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    # Prefer durable output files. Temp previews are useful only if no output node exists.
    outputs = [x for x in unique if x["type"] == "output"]
    return outputs or unique


def _safe_artifact_name(index: int, filename: str) -> str:
    name = Path(filename).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or f"artifact_{index}"
    return f"{index:02d}_{name}"


def _comfy_saved_path(item: dict[str, str]) -> Path:
    folder_type = item.get("type", "output")
    base = COMFY_OUTPUT if folder_type == "output" else COMFY_TEMP
    subfolder = item.get("subfolder") or ""
    sub = _safe_relpath(subfolder) if subfolder else Path()
    filename = _safe_relpath(item["filename"])
    source = (base / sub / filename).resolve()
    if not _inside(base, source):
        raise ValueError("saved ComfyUI path escapes its output directory")
    return source


def _copy_comfy_result(item: dict[str, str], dest: Path) -> tuple[int, str]:
    source = _comfy_saved_path(item)
    if not source.is_file():
        raise FileNotFoundError(f"ComfyUI saved output is missing: {source}")
    size = source.stat().st_size
    if size <= 0:
        raise RuntimeError(f"ComfyUI saved an empty artifact: {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    return size, content_type


def _delete_comfy_file(item: dict[str, str]) -> None:
    try:
        path = _comfy_saved_path(item)
        if path.is_file():
            path.unlink()
    except Exception:
        # Cleanup must never turn a successful generation into a failed job.
        pass


def _export_artifacts(history: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    results = _saved_results(history)
    if not results:
        keys = sorted({k for node in (history.get("outputs") or {}).values() if isinstance(node, dict) for k in node.keys()})
        raise RuntimeError(
            "Workflow completed but no saved output file descriptor was found. "
            f"Output keys: {keys}. Add a SaveVideo/SaveImage/SaveAudio output node."
        )

    job_dir = (RESULT_ROOT / run_id).resolve()
    if not _inside(RESULT_ROOT, job_dir):
        raise RuntimeError("unsafe result directory")
    job_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[dict[str, Any]] = []
    for index, item in enumerate(results, 1):
        stored_name = _safe_artifact_name(index, item["filename"])
        dest = (job_dir / stored_name).resolve()
        if not _inside(job_dir, dest):
            raise RuntimeError("unsafe artifact destination")
        size, content_type = _copy_comfy_result(item, dest)
        key = dest.relative_to(VOLUME_ROOT).as_posix()
        artifacts.append(
            {
                "filename": stored_name,
                "source_filename": Path(item["filename"]).name,
                "key": key,
                "size": size,
                "content_type": content_type,
                "comfy_type": item.get("type", "output"),
            }
        )
        _delete_comfy_file(item)
    return artifacts


def _cleanup_staged(paths: list[Path]) -> None:
    for path in paths:
        try:
            if _inside(COMFY_INPUT, path) and path.is_file():
                path.unlink()
        except Exception:
            pass


def _cleanup_stale_volume_transport() -> None:
    """Best-effort TTL cleanup for abandoned transport files, never model storage."""
    if STALE_TTL_SECONDS <= 0:
        return
    cutoff = time.time() - STALE_TTL_SECONDS
    for root in (RESULT_ROOT, INPUT_ROOT):
        if not root.is_dir():
            continue
        for child in list(root.iterdir()):
            try:
                if child.stat().st_mtime >= cutoff:
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                elif child.is_file():
                    child.unlink()
            except OSError:
                pass


def handler(job: dict[str, Any]) -> dict[str, Any]:
    payload = job.get("input") or {}
    workflow = payload.get("workflow")
    if not isinstance(workflow, dict) or not workflow:
        return {"status": "error", "error": "input.workflow must be a non-empty ComfyUI API-format workflow object"}

    run_id = re.sub(r"[^A-Za-z0-9_-]+", "_", str(job.get("id") or uuid.uuid4().hex))[:128]
    staged: list[Path] = []
    try:
        if not VOLUME_ROOT.is_dir():
            raise RuntimeError(f"Network Volume is not mounted at {VOLUME_ROOT}")
        RESULT_ROOT.mkdir(parents=True, exist_ok=True)
        INPUT_ROOT.mkdir(parents=True, exist_ok=True)
        _cleanup_stale_volume_transport()
        _wait_for_comfy()
        staged = _stage_inputs(payload)
        prompt_id = _queue_prompt(workflow, uuid.uuid4().hex)
        history = _wait_for_history(prompt_id)
        artifacts = _export_artifacts(history, run_id)
        return {
            "status": "completed",
            "prompt_id": prompt_id,
            "artifacts": artifacts,
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        _cleanup_staged(staged)


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
