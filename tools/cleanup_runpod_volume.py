from __future__ import annotations
import argparse, json, os
from pathlib import Path
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "model_manifest.json").read_text(encoding="utf-8"))
EXPECTED = {f"models/{x['path']}": int(x["size"]) for x in MANIFEST["files"]}

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
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 10, "mode": "adaptive"},
        ),
    )

def list_objects(s3, bucket):
    out, token = [], None
    while True:
        kw = {"Bucket": bucket}
        if token:
            kw["ContinuationToken"] = token
        r = s3.list_objects_v2(**kw)
        out.extend(r.get("Contents", []))
        if not r.get("IsTruncated"):
            return out
        token = r.get("NextContinuationToken")

def list_uploads(s3, bucket):
    out = []
    key_marker = None
    upload_id_marker = None
    while True:
        kw = {"Bucket": bucket}
        if key_marker:
            kw["KeyMarker"] = key_marker
        if upload_id_marker:
            kw["UploadIdMarker"] = upload_id_marker
        r = s3.list_multipart_uploads(**kw)
        out.extend(r.get("Uploads", []))
        if not r.get("IsTruncated"):
            return out
        key_marker = r.get("NextKeyMarker")
        upload_id_marker = r.get("NextUploadIdMarker")

def list_parts(s3, bucket, key, upload_id):
    out, marker = [], None
    while True:
        kw = {"Bucket": bucket, "Key": key, "UploadId": upload_id}
        if marker is not None:
            kw["PartNumberMarker"] = marker
        r = s3.list_parts(**kw)
        out.extend(r.get("Parts", []))
        if not r.get("IsTruncated"):
            return out
        marker = r.get("NextPartNumberMarker")

def gib(n):
    return n / 1024**3

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Actually abort stale/duplicate multipart uploads.")
    ap.add_argument("--delete-wrong-size", action="store_true",
                    help="Also delete completed expected objects with wrong size.")
    args = ap.parse_args()

    s3 = s3_client()
    bucket = need("RUNPOD_VOLUME_ID")

    objects = list_objects(s3, bucket)
    obj_map = {o["Key"]: int(o["Size"]) for o in objects}

    print("\nCOMPLETED OBJECTS")
    completed_bytes = 0
    wrong_size = []
    for key, expected in EXPECTED.items():
        actual = obj_map.get(key)
        if actual is None:
            print(f"MISSING  {key}")
        elif actual == expected:
            print(f"KEEP     {gib(actual):6.2f} GiB  {key}")
            completed_bytes += actual
        else:
            print(f"WRONG    {gib(actual):6.2f} GiB  expected {gib(expected):.2f}  {key}")
            completed_bytes += actual
            wrong_size.append(key)

    uploads = list_uploads(s3, bucket)
    by_key = {}
    multipart_bytes = 0

    print("\nUNFINISHED MULTIPART UPLOADS")
    for u in uploads:
        key = u["Key"]
        uid = u["UploadId"]
        parts = list_parts(s3, bucket, key, uid)
        size = sum(int(p["Size"]) for p in parts)
        multipart_bytes += size
        by_key.setdefault(key, []).append((u, size, len(parts)))

    abort = []
    resume = []

    for key, items in by_key.items():
        # newest first
        items.sort(key=lambda x: x[0].get("Initiated"), reverse=True)
        completed_exact = key in EXPECTED and obj_map.get(key) == EXPECTED[key]

        if completed_exact:
            for u, size, n in items:
                print(f"ABORT    {gib(size):6.2f} GiB  {n:4d} parts  completed object already exists  {key}")
                abort.append((key, u["UploadId"]))
            continue

        if key in EXPECTED:
            # Keep only newest unfinished upload for resume.
            newest = items[0]
            u, size, n = newest
            print(f"RESUME   {gib(size):6.2f} GiB  {n:4d} parts  {key}")
            resume.append((key, u["UploadId"], size))
            for u, size, n in items[1:]:
                print(f"ABORT    {gib(size):6.2f} GiB  {n:4d} parts  duplicate/stale  {key}")
                abort.append((key, u["UploadId"]))
        else:
            # Unknown keys are never destroyed automatically.
            for u, size, n in items:
                print(f"UNKNOWN  {gib(size):6.2f} GiB  {n:4d} parts  {key}")

    print("\nQUOTA ACCOUNTING (approximate)")
    print(f"Completed objects:   {gib(completed_bytes):.2f} GiB")
    print(f"Multipart parts:     {gib(multipart_bytes):.2f} GiB")
    print(f"Approx total stored: {gib(completed_bytes + multipart_bytes):.2f} GiB")
    print(f"Expected final H3:   {gib(sum(EXPECTED.values())):.2f} GiB")

    if wrong_size:
        print("\nWRONG-SIZE COMPLETED OBJECTS")
        for key in wrong_size:
            print(" ", key)

    if not args.apply:
        print("\nDRY RUN ONLY. Nothing was deleted.")
        print("Run again with --apply to abort only the ABORT entries above.")
        return

    print("\nAPPLYING CLEANUP")
    for key, uid in abort:
        print("Aborting:", key, uid)
        s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=uid)

    if args.delete_wrong_size:
        for key in wrong_size:
            print("Deleting wrong-size object:", key)
            s3.delete_object(Bucket=bucket, Key=key)

    print("\nCleanup complete.")
    print("Useful newest multipart uploads for missing files were preserved for resume.")

if __name__ == "__main__":
    main()
