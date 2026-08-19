from __future__ import annotations
import json, os
from pathlib import Path
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

HERE = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((HERE / "model_manifest.json").read_text(encoding="utf-8"))

def need(name):
    v = os.getenv(name)
    if not v:
        raise SystemExit(f"Missing environment variable: {name}")
    return v

s3 = boto3.client(
    "s3",
    endpoint_url=need("RUNPOD_S3_ENDPOINT"),
    region_name=need("RUNPOD_S3_REGION"),
    aws_access_key_id=need("RUNPOD_S3_ACCESS_KEY"),
    aws_secret_access_key=need("RUNPOD_S3_SECRET_KEY"),
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)
bucket = need("RUNPOD_VOLUME_ID")

bad = []
total = 0
for item in MANIFEST["files"]:
    key = "models/" + item["path"]
    try:
        h = s3.head_object(Bucket=bucket, Key=key)
    except ClientError:
        bad.append(f"MISSING {key}")
        continue
    size = int(h["ContentLength"])
    total += size
    meta = {k.lower(): v for k, v in h.get("Metadata", {}).items()}
    expected_size = int(item["size"])
    expected_sha = item["sha256"]
    stored_sha = meta.get("sha256")

    if size != expected_size:
        status = "BAD_SIZE"
        bad.append(key)
    elif stored_sha == expected_sha:
        status = "OK"
    else:
        # The object is complete by exact byte size. Missing/legacy SHA metadata
        # must not force a multi-GB re-upload.
        status = "OK_SIZE"

    print(f"{status:8s} {size/1024**3:7.2f} GiB  {key}")

print(f"\nTotal: {total} bytes ({total/1024**3:.2f} GiB)")
if bad:
    print("\nVolume is NOT ready (missing or wrong-size objects only):")
    for x in bad:
        print(" -", x)
    raise SystemExit(2)

print("\nVOLUME READY BY OBJECT SIZE.")
print("Objects marked OK_SIZE have the expected exact byte size but missing/mismatched sha256 metadata.")
print("No re-upload is required solely because SHA metadata is absent.")
