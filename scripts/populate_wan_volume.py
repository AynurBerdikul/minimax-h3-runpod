from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import requests


ROOT = Path(os.getenv("WAN_MODEL_ROOT", "/runpod-volume/models"))
MANIFEST = Path("/opt/h3/model_manifest.json")
LOCK = Path("/runpod-volume/.wan-model-bootstrap.lock")
REPORT_BYTES = 1024**3


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, target: Path, expected: int) -> None:
    partial = target.with_name(target.name + ".part")
    partial.parent.mkdir(parents=True, exist_ok=True)
    current = partial.stat().st_size if partial.exists() else 0
    if current > expected:
        partial.unlink()
        current = 0

    for attempt in range(6):
        headers = {"Range": f"bytes={current}-"} if current else {}
        try:
            with requests.get(url, headers=headers, stream=True, timeout=(30, 300)) as response:
                response.raise_for_status()
                if current and response.status_code != 206:
                    partial.unlink(missing_ok=True)
                    current = 0
                    continue
                done = current
                next_report = done + REPORT_BYTES
                with partial.open("ab" if current else "wb") as handle:
                    for chunk in response.iter_content(chunk_size=16 * 1024 * 1024):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        done += len(chunk)
                        if done >= next_report:
                            print(f"[WAN] Downloaded {target.name}: {done / 2**30:.1f}/{expected / 2**30:.1f} GiB", flush=True)
                            next_report += REPORT_BYTES
                if partial.stat().st_size != expected:
                    raise RuntimeError(f"downloaded size {partial.stat().st_size} != {expected}")
                return
        except Exception as exc:
            current = partial.stat().st_size if partial.exists() else 0
            if attempt == 5:
                raise RuntimeError(f"download failed for {target.name}: {exc}") from exc
            delay = min(60, 2 ** (attempt + 1))
            print(f"[WAN] Download retry {attempt + 1}/5 for {target.name} in {delay}s: {exc}", flush=True)
            time.sleep(delay)


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    ROOT.mkdir(parents=True, exist_ok=True)
    LOCK.parent.mkdir(parents=True, exist_ok=True)

    with LOCK.open("a+b") as lock_handle:
        print("[WAN] Waiting for exclusive model-volume bootstrap lock...", flush=True)
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        print("[WAN] Model-volume bootstrap lock acquired.", flush=True)

        required = sum(int(item["size"]) for item in manifest["files"])
        missing = []
        for item in manifest["files"]:
            target = ROOT / item["path"]
            if not target.exists() or target.stat().st_size != int(item["size"]):
                missing.append(item)
        needed = 0
        for item in missing:
            target = ROOT / item["path"]
            partial = target.with_name(target.name + ".part")
            partial_size = partial.stat().st_size if partial.exists() else 0
            needed += max(0, int(item["size"]) - partial_size)
        free = shutil.disk_usage(ROOT).free
        print(
            f"[WAN] Files ready: {len(manifest['files']) - len(missing)}/{len(manifest['files'])}; "
            f"need {needed / 2**30:.1f} GiB; free {free / 2**30:.1f} GiB.",
            flush=True,
        )
        if free < needed:
            raise RuntimeError(f"Network Volume has insufficient free space: {free} < {needed}")

        default_repo = manifest["repo_id"]
        default_revision = manifest["repo_revision"]
        for index, item in enumerate(missing, start=1):
            target = ROOT / item["path"]
            expected = int(item["size"])
            if target.exists():
                target.unlink()
            repo = item.get("repo_id", default_repo)
            revision = item.get("repo_revision", default_revision)
            source = item["source"]
            url = f"https://huggingface.co/{repo}/resolve/{revision}/{source}"
            print(f"[WAN] ({index}/{len(missing)}) Downloading {item['path']}", flush=True)
            download(url, target, expected)
            partial = target.with_name(target.name + ".part")
            actual_sha = sha256(partial)
            if actual_sha != item["sha256"]:
                partial.unlink(missing_ok=True)
                raise RuntimeError(f"SHA256 mismatch for {item['path']}: {actual_sha}")
            os.replace(partial, target)
            print(f"[WAN] Verified {item['path']}", flush=True)

        print(f"[WAN] Volume population complete: {required / 2**30:.1f} GiB verified.", flush=True)


if __name__ == "__main__":
    main()
