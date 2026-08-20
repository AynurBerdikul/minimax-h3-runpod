from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import boto3
import requests
from botocore.config import Config
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "model_manifest.json").read_text(encoding="utf-8"))
PART_SIZE = 64 * 1024 * 1024
HTTP_CHUNK = 8 * 1024 * 1024


def need(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing environment variable: {name}")
    return value


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
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise
    return int(head.get("ContentLength", -1))


def list_parts(s3, bucket: str, key: str, upload_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    marker = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Key": key, "UploadId": upload_id}
        if marker is not None:
            kwargs["PartNumberMarker"] = marker
        response = s3.list_parts(**kwargs)
        out.extend(response.get("Parts", []))
        if not response.get("IsTruncated"):
            return sorted(out, key=lambda item: int(item["PartNumber"]))
        marker = response.get("NextPartNumberMarker")
        if marker is None:
            raise RuntimeError("Multipart part listing was truncated without NextPartNumberMarker")


def _multipart_candidates(s3, bucket: str, key: str) -> list[dict[str, Any]]:
    """List all matching uploads, tolerating RunPod's occasional leading slash."""
    candidates: list[dict[str, Any]] = []
    key_marker = None
    upload_marker = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": key}
        if key_marker:
            kwargs["KeyMarker"] = key_marker
        if upload_marker:
            kwargs["UploadIdMarker"] = upload_marker
        response = s3.list_multipart_uploads(**kwargs)
        for upload in response.get("Uploads", []):
            raw_key = str(upload.get("Key") or "")
            if raw_key.lstrip("/") == key.lstrip("/"):
                candidates.append(upload)
        if not response.get("IsTruncated"):
            break
        key_marker = response.get("NextKeyMarker")
        upload_marker = response.get("NextUploadIdMarker")
        if not key_marker and not upload_marker:
            break
    return candidates


def discover_upload(s3, bucket: str, key: str, expected: int) -> tuple[str | None, list[dict[str, Any]]]:
    """Resume the most-complete safe upload, not merely the most recent one."""
    ranked: list[tuple[int, str, list[dict[str, Any]]]] = []
    for upload in _multipart_candidates(s3, bucket, key):
        upload_id = str(upload.get("UploadId") or "")
        if not upload_id:
            continue
        try:
            parts = list_parts(s3, bucket, key, upload_id)
        except ClientError:
            continue
        uploaded = sum(int(part.get("Size", 0)) for part in parts)
        safe = uploaded <= expected
        if parts and uploaded < expected:
            safe = safe and all(int(part.get("Size", 0)) == PART_SIZE for part in parts)
        if safe:
            ranked.append((uploaded, upload_id, parts))

    if not ranked:
        return None, []
    ranked.sort(key=lambda row: row[0], reverse=True)
    _, upload_id, parts = ranked[0]
    return upload_id, parts


def new_upload(s3, bucket: str, key: str, item: dict[str, Any]) -> str:
    response = s3.create_multipart_upload(
        Bucket=bucket,
        Key=key,
        ContentType="application/octet-stream",
        Metadata={"sha256": item["sha256"], "source": MANIFEST["repo_id"]},
    )
    return str(response["UploadId"])


def source_url(path: str) -> str:
    return f"https://huggingface.co/{MANIFEST['repo_id']}/resolve/{MANIFEST['repo_revision']}/{path}"


def upload_one(s3, bucket: str, item: dict[str, Any]) -> None:
    path = item["path"]
    key = "models/" + path
    expected = int(item["size"])

    actual = remote_size(s3, bucket, key)
    if actual == expected:
        print(f"OK existing: {key} ({actual} bytes)")
        return
    if actual is not None:
        print(f"BAD existing size: {key}: {actual} != {expected}; replacing")
        s3.delete_object(Bucket=bucket, Key=key)

    upload_id, parts = discover_upload(s3, bucket, key, expected)
    if not upload_id:
        upload_id = new_upload(s3, bucket, key, item)
        parts = []

    uploaded = sum(int(part["Size"]) for part in parts)
    headers: dict[str, str] = {}
    if os.getenv("HF_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['HF_TOKEN']}"
    if uploaded:
        headers["Range"] = f"bytes={uploaded}-"

    print(f"{key}: resume {uploaded / 1024**3:.2f} / {expected / 1024**3:.2f} GiB")
    with requests.get(source_url(path), headers=headers, stream=True, allow_redirects=True, timeout=(30, 600)) as response:
        response.raise_for_status()
        if uploaded and response.status_code != 206:
            raise RuntimeError("Hugging Face did not honor Range request")

        complete = [{"PartNumber": int(p["PartNumber"]), "ETag": p["ETag"]} for p in parts]
        next_part = int(parts[-1]["PartNumber"]) + 1 if parts else 1
        buffer = bytearray()
        transferred = uploaded

        for chunk in response.iter_content(chunk_size=HTTP_CHUNK):
            if not chunk:
                continue
            buffer.extend(chunk)
            while len(buffer) >= PART_SIZE:
                body = bytes(buffer[:PART_SIZE])
                del buffer[:PART_SIZE]
                part = s3.upload_part(Bucket=bucket, Key=key, UploadId=upload_id, PartNumber=next_part, Body=body)
                complete.append({"PartNumber": next_part, "ETag": part["ETag"]})
                transferred += len(body)
                next_part += 1
                print(f"{transferred / 1024**3:.2f}/{expected / 1024**3:.2f} GiB", flush=True)

        if buffer:
            body = bytes(buffer)
            part = s3.upload_part(Bucket=bucket, Key=key, UploadId=upload_id, PartNumber=next_part, Body=body)
            complete.append({"PartNumber": next_part, "ETag": part["ETag"]})
            transferred += len(body)

    if transferred != expected:
        raise RuntimeError(f"Size mismatch before completion: {transferred} != {expected}")

    s3.complete_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id, MultipartUpload={"Parts": complete})
    actual = remote_size(s3, bucket, key)
    if actual != expected:
        raise RuntimeError(f"Final size verification failed: {actual} != {expected}")
    print(f"DONE: {key} ({actual} bytes)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file")
    args = parser.parse_args()

    if os.getenv("ACCEPT_MINIMAX_H3_LICENSE") != "YES":
        raise SystemExit("Set ACCEPT_MINIMAX_H3_LICENSE=YES after reviewing the license.")

    items = MANIFEST["files"]
    if args.file:
        items = [item for item in items if item["path"] == args.file]
        if not items:
            raise SystemExit("File not found in manifest")

    s3 = s3_client()
    bucket = need("RUNPOD_VOLUME_ID")
    for item in items:
        attempts = 0
        while True:
            try:
                upload_one(s3, bucket, item)
                break
            except (requests.RequestException, ClientError) as exc:
                attempts += 1
                if attempts >= 5:
                    raise
                delay = min(60, 5 * (2 ** (attempts - 1)))
                print("Transient error; retrying in", delay, "s:", exc)
                time.sleep(delay)


if __name__ == "__main__":
    main()
