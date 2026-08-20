from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import PurePosixPath
from typing import Any

import boto3
import requests
from botocore.config import Config
from botocore.exceptions import ClientError

PART_SIZE = 64 * 1024 * 1024
HTTP_CHUNK = 8 * 1024 * 1024
ALLOWED_TOP = {
    "checkpoints", "clip", "clip_vision", "controlnet", "diffusion_models",
    "embeddings", "loras", "model_patches", "text_encoders", "unet", "vae",
    "vae_approx", "upscale_models", "audio_encoders", "style_models",
}


def need(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing environment variable: {name}")
    return value


def safe_model_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/").strip("/"))
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise SystemExit(f"Unsafe model path: {value!r}")
    if path.parts[0] not in ALLOWED_TOP:
        raise SystemExit(f"Unsupported model directory: {path.parts[0]}")
    return path.as_posix()


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=need("RUNPOD_S3_ENDPOINT"),
        region_name=need("RUNPOD_S3_REGION"),
        aws_access_key_id=need("RUNPOD_S3_ACCESS_KEY"),
        aws_secret_access_key=need("RUNPOD_S3_SECRET_KEY"),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 10, "mode": "adaptive"},
        ),
    )


def remote_size(s3, bucket: str, key: str) -> int | None:
    try:
        response = s3.head_object(Bucket=bucket, Key=key)
        return int(response.get("ContentLength", -1))
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def source_url(repo_id: str, revision: str, path: str) -> str:
    return f"https://huggingface.co/{repo_id}/resolve/{revision}/{path}"


def hf_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if os.getenv("HF_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['HF_TOKEN']}"
    return headers


def source_size(url: str, headers: dict[str, str]) -> int:
    response = requests.head(url, headers=headers, allow_redirects=True, timeout=(30, 120))
    response.raise_for_status()
    size = int(response.headers.get("Content-Length") or 0)
    if size > 0:
        return size

    probe_headers = dict(headers)
    probe_headers["Range"] = "bytes=0-0"
    with requests.get(url, headers=probe_headers, stream=True, allow_redirects=True, timeout=(30, 120)) as probe:
        probe.raise_for_status()
        content_range = probe.headers.get("Content-Range", "")
        if "/" in content_range:
            total = content_range.rsplit("/", 1)[1]
            if total.isdigit() and int(total) > 0:
                return int(total)
        size = int(probe.headers.get("Content-Length") or 0)
        if probe.status_code == 200 and size > 0:
            return size
    raise RuntimeError("Hugging Face did not expose the source file size")


def list_parts(s3, bucket: str, key: str, upload_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    marker = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Key": key, "UploadId": upload_id}
        if marker is not None:
            kwargs["PartNumberMarker"] = marker
        response = s3.list_parts(**kwargs)
        result.extend(response.get("Parts", []))
        if not response.get("IsTruncated"):
            return sorted(result, key=lambda part: int(part["PartNumber"]))
        marker = response.get("NextPartNumberMarker")
        if marker is None:
            raise RuntimeError("Truncated multipart part listing without continuation marker")


def multipart_candidates(s3, bucket: str, key: str) -> list[dict[str, Any]]:
    response = s3.list_multipart_uploads(Bucket=bucket, Prefix=key)
    return [
        item for item in response.get("Uploads", [])
        if str(item.get("Key") or "").lstrip("/") == key.lstrip("/")
    ]


def discover_upload(s3, bucket: str, key: str, total: int) -> tuple[str | None, list[dict[str, Any]]]:
    ranked: list[tuple[int, str, list[dict[str, Any]]]] = []
    for item in multipart_candidates(s3, bucket, key):
        upload_id = str(item.get("UploadId") or "")
        if not upload_id:
            continue
        try:
            parts = list_parts(s3, bucket, key, upload_id)
        except ClientError:
            continue
        uploaded = sum(int(part.get("Size", 0)) for part in parts)
        safe = uploaded <= total
        if parts and uploaded < total:
            safe = safe and all(int(part.get("Size", 0)) == PART_SIZE for part in parts)
        if safe:
            ranked.append((uploaded, upload_id, parts))
    if not ranked:
        return None, []
    ranked.sort(key=lambda row: row[0], reverse=True)
    _, upload_id, parts = ranked[0]
    return upload_id, parts


def create_upload(s3, bucket: str, key: str, repo_id: str, revision: str) -> str:
    created = s3.create_multipart_upload(
        Bucket=bucket,
        Key=key,
        ContentType="application/octet-stream",
        Metadata={"source_repo": repo_id, "source_revision": revision},
    )
    return str(created["UploadId"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream one Hugging Face model directly to a RunPod Network Volume.")
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--revision", default="main")
    parser.add_argument("--path", required=True)
    args = parser.parse_args()

    path = safe_model_path(args.path)
    key = f"models/{path}"
    bucket = need("RUNPOD_VOLUME_ID")
    s3 = s3_client()
    headers = hf_headers()
    url = source_url(args.repo_id, args.revision, path)
    total = source_size(url, headers)

    print(f"Source: {url}")
    print(f"Destination: s3://{bucket}/{key}")
    print(f"Expected size: {total} bytes ({total / 1024**3:.2f} GiB)")

    existing = remote_size(s3, bucket, key)
    if existing == total:
        print(json.dumps({"status": "existing", "path": path, "key": key, "size": existing}, sort_keys=True))
        return
    if existing is not None:
        print(f"Replacing incomplete/wrong-size final object: {existing} bytes")
        s3.delete_object(Bucket=bucket, Key=key)

    upload_id, parts = discover_upload(s3, bucket, key, total)
    if not upload_id:
        upload_id = create_upload(s3, bucket, key, args.repo_id, args.revision)
        parts = []

    uploaded_bytes = sum(int(part["Size"]) for part in parts)
    request_headers = dict(headers)
    if uploaded_bytes:
        request_headers["Range"] = f"bytes={uploaded_bytes}-"

    digest = hashlib.sha256() if uploaded_bytes == 0 else None
    completed = [{"PartNumber": int(p["PartNumber"]), "ETag": p["ETag"]} for p in parts]
    next_part = int(parts[-1]["PartNumber"]) + 1 if parts else 1
    transferred = uploaded_bytes
    buffer = bytearray()

    print(f"Resume: {uploaded_bytes / 1024**3:.2f} / {total / 1024**3:.2f} GiB")
    with requests.get(url, headers=request_headers, stream=True, allow_redirects=True, timeout=(30, 600)) as response:
        response.raise_for_status()
        if uploaded_bytes and response.status_code != 206:
            raise RuntimeError("Hugging Face did not honor the resume Range request")
        for chunk in response.iter_content(chunk_size=HTTP_CHUNK):
            if not chunk:
                continue
            if digest is not None:
                digest.update(chunk)
            buffer.extend(chunk)
            while len(buffer) >= PART_SIZE:
                body = bytes(buffer[:PART_SIZE])
                del buffer[:PART_SIZE]
                result = s3.upload_part(
                    Bucket=bucket, Key=key, UploadId=upload_id,
                    PartNumber=next_part, Body=body,
                )
                completed.append({"PartNumber": next_part, "ETag": result["ETag"]})
                next_part += 1
                transferred += len(body)
                print(f"{transferred / 1024**3:.2f}/{total / 1024**3:.2f} GiB", flush=True)

        if buffer:
            body = bytes(buffer)
            result = s3.upload_part(
                Bucket=bucket, Key=key, UploadId=upload_id,
                PartNumber=next_part, Body=body,
            )
            completed.append({"PartNumber": next_part, "ETag": result["ETag"]})
            transferred += len(body)

    if transferred != total:
        # Intentionally leave multipart open: the next run resumes it.
        raise RuntimeError(f"Transfer size mismatch: {transferred} != {total}; multipart preserved for resume")

    s3.complete_multipart_upload(
        Bucket=bucket,
        Key=key,
        UploadId=upload_id,
        MultipartUpload={"Parts": completed},
    )
    final_size = remote_size(s3, bucket, key)
    if final_size != total:
        raise RuntimeError(f"Final RunPod object size mismatch: {final_size} != {total}")

    result = {
        "status": "uploaded",
        "path": path,
        "key": key,
        "size": total,
        "sha256": digest.hexdigest() if digest is not None else None,
        "resumed": uploaded_bytes > 0,
    }
    print("MODEL_UPLOAD_RESULT=" + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
