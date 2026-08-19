"""
Populate a RunPod Network Volume without starting a GPU worker.

Data path:
    Hugging Face HTTPS -> this process (small RAM buffer) -> RunPod S3 API

No complete model file is written to local disk.

Required environment variables:
    RUNPOD_S3_ACCESS_KEY
    RUNPOD_S3_SECRET_KEY
    RUNPOD_VOLUME_ID
    RUNPOD_S3_ENDPOINT
    RUNPOD_S3_REGION

Optional:
    HF_TOKEN

Install once:
    py -m pip install boto3 requests

The script is resumable. It stores only multipart upload IDs and counters in a
small local JSON state file; it never stores model data locally.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import boto3
import requests
from botocore.config import Config
from botocore.exceptions import ClientError

HERE = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((HERE / "model_manifest.json").read_text(encoding="utf-8"))
STATE_FILE = HERE / ".runpod_h3_upload_state.json"
PART_SIZE = 64 * 1024 * 1024
HTTP_CHUNK = 8 * 1024 * 1024

def need(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing environment variable: {name}")
    return value

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}

def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

def make_s3():
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

def remote_is_valid(s3, bucket: str, key: str, item: dict) -> bool:
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError:
        return False
    if int(head.get("ContentLength", -1)) != int(item["size"]):
        return False
    meta = {k.lower(): v for k, v in head.get("Metadata", {}).items()}
    return meta.get("sha256") == item["sha256"]

def list_uploaded_parts(s3, bucket: str, key: str, upload_id: str):
    parts = []
    marker = None
    while True:
        kwargs = dict(Bucket=bucket, Key=key, UploadId=upload_id)
        if marker is not None:
            kwargs["PartNumberMarker"] = marker
        resp = s3.list_parts(**kwargs)
        parts.extend(resp.get("Parts", []))
        if not resp.get("IsTruncated"):
            break
        marker = resp.get("NextPartNumberMarker")
    return sorted(parts, key=lambda p: p["PartNumber"])

def hf_headers(offset: int = 0) -> dict:
    h = {}
    token = os.getenv("HF_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    if offset:
        h["Range"] = f"bytes={offset}-"
    return h

def source_url(repo_path: str) -> str:
    return f"https://huggingface.co/{MANIFEST['repo_id']}/resolve/{MANIFEST['repo_revision']}/{repo_path}"

def upload_one(s3, bucket: str, item: dict, state: dict):
    repo_path = item["path"]
    key = f"models/{repo_path}"
    expected_size = int(item["size"])
    expected_sha = item["sha256"]

    if remote_is_valid(s3, bucket, key, item):
        print(f"OK existing: {key}")
        state.pop(key, None)
        save_state(state)
        return

    entry = state.get(key)
    upload_id = entry.get("upload_id") if entry else None

    if upload_id:
        try:
            existing_parts = list_uploaded_parts(s3, bucket, key, upload_id)
        except ClientError:
            existing_parts = []
            upload_id = None
    else:
        existing_parts = []

    if not upload_id:
        resp = s3.create_multipart_upload(
            Bucket=bucket,
            Key=key,
            Metadata={
                "sha256": expected_sha,
                "source": MANIFEST["repo_id"],
            },
            ContentType="application/octet-stream",
        )
        upload_id = resp["UploadId"]
        existing_parts = []
        state[key] = {"upload_id": upload_id}
        save_state(state)

    uploaded_bytes = sum(int(p["Size"]) for p in existing_parts)
    next_part = (existing_parts[-1]["PartNumber"] + 1) if existing_parts else 1

    # All already-uploaded parts must be full PART_SIZE except possibly if the
    # object had somehow completed. A short intermediate part is unsafe to resume.
    if existing_parts[:-1] and any(int(p["Size"]) != PART_SIZE for p in existing_parts[:-1]):
        raise RuntimeError(f"Unsafe multipart state for {key}; abort it manually and retry.")

    # If the latest part is short while there is still source data remaining,
    # restart this file cleanly to avoid corrupt byte offsets.
    if existing_parts and int(existing_parts[-1]["Size"]) != PART_SIZE and uploaded_bytes < expected_size:
        print(f"Restarting incomplete short-part upload for {key}")
        s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        state.pop(key, None)
        save_state(state)
        return upload_one(s3, bucket, item, state)

    if uploaded_bytes > expected_size:
        raise RuntimeError(f"Remote multipart data exceeds expected size for {key}")

    print(f"\n{key}")
    print(f"Expected: {expected_size/1024**3:.2f} GiB; resume at {uploaded_bytes/1024**3:.2f} GiB")

    url = source_url(repo_path)
    with requests.get(
        url,
        headers=hf_headers(uploaded_bytes),
        stream=True,
        allow_redirects=True,
        timeout=(30, 600),
    ) as response:
        response.raise_for_status()

        # When resuming, Hugging Face/Xet must honor Range.
        if uploaded_bytes and response.status_code != 206:
            raise RuntimeError(f"Source did not honor Range request for {repo_path}")

        parts_for_complete = [
            {"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in existing_parts
        ]
        buffer = bytearray()
        transferred = uploaded_bytes

        for chunk in response.iter_content(chunk_size=HTTP_CHUNK):
            if not chunk:
                continue
            buffer.extend(chunk)

            while len(buffer) >= PART_SIZE:
                body = bytes(buffer[:PART_SIZE])
                del buffer[:PART_SIZE]
                resp = s3.upload_part(
                    Bucket=bucket, Key=key, UploadId=upload_id,
                    PartNumber=next_part, Body=body,
                )
                parts_for_complete.append({"PartNumber": next_part, "ETag": resp["ETag"]})
                transferred += len(body)
                next_part += 1
                print(f"  {transferred/1024**3:.2f}/{expected_size/1024**3:.2f} GiB", end="\r")

        if buffer:
            body = bytes(buffer)
            resp = s3.upload_part(
                Bucket=bucket, Key=key, UploadId=upload_id,
                PartNumber=next_part, Body=body,
            )
            parts_for_complete.append({"PartNumber": next_part, "ETag": resp["ETag"]})
            transferred += len(body)

    if transferred != expected_size:
        raise RuntimeError(f"Transferred size mismatch for {key}: {transferred} != {expected_size}")

    s3.complete_multipart_upload(
        Bucket=bucket,
        Key=key,
        UploadId=upload_id,
        MultipartUpload={"Parts": parts_for_complete},
    )

    # Verify final object by exact size + metadata.
    if not remote_is_valid(s3, bucket, key, item):
        raise RuntimeError(f"Final S3 verification failed for {key}")

    state.pop(key, None)
    save_state(state)
    print(f"\nDONE: {key}")

def main():
    if os.getenv("ACCEPT_MINIMAX_H3_LICENSE") != "YES":
        raise SystemExit(
            "Set ACCEPT_MINIMAX_H3_LICENSE=YES only after reviewing the MiniMax H3 license."
        )

    s3 = make_s3()
    bucket = need("RUNPOD_VOLUME_ID")
    state = load_state()

    for item in MANIFEST["files"]:
        # Retry transient network failures without starting over from completed parts.
        attempts = 0
        while True:
            try:
                upload_one(s3, bucket, item, state)
                break
            except (requests.RequestException, ClientError) as exc:
                attempts += 1
                if attempts >= 5:
                    raise
                wait = min(60, 5 * (2 ** (attempts - 1)))
                print(f"\nTransient error: {exc}\nRetrying in {wait}s...")
                time.sleep(wait)

    print("\nAll H3 model files are present and verified by exact size + stored SHA metadata.")

if __name__ == "__main__":
    main()
