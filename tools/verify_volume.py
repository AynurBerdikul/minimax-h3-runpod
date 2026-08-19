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
    ok = size == int(item["size"]) and meta.get("sha256") == item["sha256"]
    print(("OK " if ok else "BAD"), f"{size/1024**3:7.2f} GiB  {key}")
    if not ok:
        bad.append(key)

print(f"\nTotal: {total} bytes ({total/1024**3:.2f} GiB)")
if bad:
    print("\nVolume is NOT ready:")
    for x in bad:
        print(" -", x)
    raise SystemExit(2)

print("\nVOLUME READY. It is safe to start the Serverless worker.")
