from __future__ import annotations
import argparse, json, os, time
from pathlib import Path
import boto3, requests
from botocore.config import Config
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "model_manifest.json").read_text(encoding="utf-8"))
PART_SIZE = 64 * 1024 * 1024
HTTP_CHUNK = 8 * 1024 * 1024

def need(name):
    v = os.getenv(name)
    if not v:
        raise SystemExit(f"Missing environment variable: {name}")
    return v

def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=need("RUNPOD_S3_ENDPOINT"),
        region_name=need("RUNPOD_S3_REGION"),
        aws_access_key_id=need("RUNPOD_S3_ACCESS_KEY"),
        aws_secret_access_key=need("RUNPOD_S3_SECRET_KEY"),
        config=Config(signature_version="s3v4",
                      s3={"addressing_style":"path"},
                      retries={"max_attempts":10,"mode":"adaptive"}),
    )

def remote_size(s3, bucket, key):
    try:
        h = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as e:
        status = e.response.get("ResponseMetadata",{}).get("HTTPStatusCode")
        code = str(e.response.get("Error",{}).get("Code",""))
        if status == 404 or code in {"404","NoSuchKey","NotFound"}:
            return None
        raise
    return int(h.get("ContentLength",-1))

def list_parts(s3, bucket, key, upload_id):
    out, marker = [], None
    while True:
        kw = {"Bucket":bucket,"Key":key,"UploadId":upload_id}
        if marker is not None:
            kw["PartNumberMarker"] = marker
        r = s3.list_parts(**kw)
        out += r.get("Parts",[])
        if not r.get("IsTruncated"):
            return sorted(out, key=lambda x:x["PartNumber"])
        marker = r.get("NextPartNumberMarker")

def discover_upload(s3, bucket, key):
    r = s3.list_multipart_uploads(Bucket=bucket, Prefix=key)
    uploads = [u for u in r.get("Uploads",[]) if u.get("Key")==key]
    if not uploads:
        return None
    uploads.sort(key=lambda u:u.get("Initiated"), reverse=True)
    return uploads[0]["UploadId"]

def new_upload(s3, bucket, key, item):
    r = s3.create_multipart_upload(
        Bucket=bucket, Key=key, ContentType="application/octet-stream",
        Metadata={"sha256":item["sha256"],"source":MANIFEST["repo_id"]})
    return r["UploadId"]

def source_url(path):
    return f"https://huggingface.co/{MANIFEST['repo_id']}/resolve/{MANIFEST['repo_revision']}/{path}"

def upload_one(s3, bucket, item):
    path = item["path"]
    key = "models/" + path
    expected = int(item["size"])

    actual = remote_size(s3,bucket,key)
    if actual == expected:
        print(f"OK existing: {key} ({actual} bytes)")
        return

    if actual is not None:
        print(f"BAD existing size: {key}: {actual} != {expected}; replacing")
        s3.delete_object(Bucket=bucket, Key=key)

    upload_id = discover_upload(s3,bucket,key)
    parts = list_parts(s3,bucket,key,upload_id) if upload_id else []
    if not upload_id:
        upload_id = new_upload(s3,bucket,key,item)

    uploaded = sum(int(p["Size"]) for p in parts)
    unsafe = any(int(p["Size"]) != PART_SIZE for p in parts[:-1])
    if parts and int(parts[-1]["Size"]) != PART_SIZE and uploaded < expected:
        unsafe = True

    if unsafe or uploaded > expected:
        s3.abort_multipart_upload(Bucket=bucket,Key=key,UploadId=upload_id)
        upload_id = new_upload(s3,bucket,key,item)
        parts, uploaded = [], 0

    headers = {}
    if os.getenv("HF_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['HF_TOKEN']}"
    if uploaded:
        headers["Range"] = f"bytes={uploaded}-"

    print(f"{key}: resume {uploaded/1024**3:.2f} / {expected/1024**3:.2f} GiB")

    with requests.get(source_url(path), headers=headers, stream=True,
                      allow_redirects=True, timeout=(30,600)) as r:
        r.raise_for_status()
        if uploaded and r.status_code != 206:
            raise RuntimeError("Hugging Face did not honor Range request")

        complete = [{"PartNumber":p["PartNumber"],"ETag":p["ETag"]} for p in parts]
        next_part = parts[-1]["PartNumber"]+1 if parts else 1
        buf = bytearray()
        transferred = uploaded

        for chunk in r.iter_content(chunk_size=HTTP_CHUNK):
            if not chunk:
                continue
            buf.extend(chunk)
            while len(buf) >= PART_SIZE:
                body = bytes(buf[:PART_SIZE]); del buf[:PART_SIZE]
                part = s3.upload_part(Bucket=bucket,Key=key,UploadId=upload_id,
                                      PartNumber=next_part,Body=body)
                complete.append({"PartNumber":next_part,"ETag":part["ETag"]})
                transferred += len(body); next_part += 1
                print(f"{transferred/1024**3:.2f}/{expected/1024**3:.2f} GiB", flush=True)

        if buf:
            body = bytes(buf)
            part = s3.upload_part(Bucket=bucket,Key=key,UploadId=upload_id,
                                  PartNumber=next_part,Body=body)
            complete.append({"PartNumber":next_part,"ETag":part["ETag"]})
            transferred += len(body)

    if transferred != expected:
        raise RuntimeError(f"Size mismatch before completion: {transferred} != {expected}")

    s3.complete_multipart_upload(Bucket=bucket,Key=key,UploadId=upload_id,
                                 MultipartUpload={"Parts":complete})

    actual = remote_size(s3,bucket,key)
    if actual != expected:
        raise RuntimeError(f"Final size verification failed: {actual} != {expected}")

    print(f"DONE: {key} ({actual} bytes)")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    args = ap.parse_args()

    if os.getenv("ACCEPT_MINIMAX_H3_LICENSE") != "YES":
        raise SystemExit("Set ACCEPT_MINIMAX_H3_LICENSE=YES after reviewing the license.")

    items = MANIFEST["files"]
    if args.file:
        items = [x for x in items if x["path"] == args.file]
        if not items:
            raise SystemExit("File not found in manifest")

    s3 = s3_client()
    bucket = need("RUNPOD_VOLUME_ID")

    for item in items:
        attempts = 0
        while True:
            try:
                upload_one(s3,bucket,item)
                break
            except (requests.RequestException, ClientError) as e:
                attempts += 1
                if attempts >= 5:
                    raise
                delay = min(60, 5*(2**(attempts-1)))
                print("Transient error; retrying in", delay, "s:", e)
                time.sleep(delay)

if __name__ == "__main__":
    main()
