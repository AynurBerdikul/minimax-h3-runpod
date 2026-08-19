from __future__ import annotations
import argparse, json, os
from pathlib import Path

import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads(
    (ROOT / "model_manifest.json").read_text(encoding="utf-8")
)
EXPECTED = {
    f"models/{x['path']}": int(x["size"])
    for x in MANIFEST["files"]
}


def need(name):
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing environment variable: {name}")
    return value


def normalize_key(key):
    # RunPod can return multipart keys with a leading "/".
    return key.lstrip("/")


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
    result = []
    token = None

    while True:
        kwargs = {"Bucket": bucket}

        if token:
            kwargs["ContinuationToken"] = token

        response = s3.list_objects_v2(**kwargs)
        result.extend(response.get("Contents", []))

        if not response.get("IsTruncated"):
            return result

        token = response["NextContinuationToken"]


def list_uploads(s3, bucket):
    result = []
    key_marker = None
    upload_marker = None

    while True:
        kwargs = {"Bucket": bucket}

        if key_marker:
            kwargs["KeyMarker"] = key_marker

        if upload_marker:
            kwargs["UploadIdMarker"] = upload_marker

        response = s3.list_multipart_uploads(**kwargs)
        result.extend(response.get("Uploads", []))

        if not response.get("IsTruncated"):
            return result

        key_marker = response.get("NextKeyMarker")
        upload_marker = response.get("NextUploadIdMarker")


def list_parts(s3, bucket, raw_key, upload_id):
    result = []
    marker = None

    while True:
        kwargs = {
            "Bucket": bucket,
            "Key": raw_key,
            "UploadId": upload_id,
        }

        if marker is not None:
            kwargs["PartNumberMarker"] = marker

        response = s3.list_parts(**kwargs)
        result.extend(response.get("Parts", []))

        if not response.get("IsTruncated"):
            return result

        marker = response.get("NextPartNumberMarker")


def gib(value):
    return value / 1024**3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    s3 = s3_client()
    bucket = need("RUNPOD_VOLUME_ID")

    objects = list_objects(s3, bucket)

    obj_map = {
        normalize_key(obj["Key"]): int(obj["Size"])
        for obj in objects
    }

    print("\nCOMPLETED OBJECTS")

    completed_bytes = 0

    for key, expected_size in EXPECTED.items():
        actual_size = obj_map.get(key)

        if actual_size is None:
            print("MISSING ", key)

        elif actual_size == expected_size:
            print(
                f"KEEP    {gib(actual_size):6.2f} GiB  {key}"
            )
            completed_bytes += actual_size

        else:
            print(
                f"WRONG   {gib(actual_size):6.2f} GiB "
                f"expected {gib(expected_size):.2f}  {key}"
            )
            completed_bytes += actual_size

    uploads = list_uploads(s3, bucket)

    grouped = {}
    multipart_bytes = 0

    for upload in uploads:

        raw_key = upload["Key"]
        key = normalize_key(raw_key)

        parts = list_parts(
            s3,
            bucket,
            raw_key,
            upload["UploadId"],
        )

        size = sum(int(p["Size"]) for p in parts)

        multipart_bytes += size

        grouped.setdefault(key, []).append(
            (
                upload,
                raw_key,
                size,
                len(parts),
            )
        )

    abort = []

    print("\nMULTIPART UPLOADS")

    for key, items in grouped.items():

        items.sort(
            key=lambda x: x[0].get("Initiated"),
            reverse=True,
        )

        completed_exact = (
            key in EXPECTED
            and obj_map.get(key) == EXPECTED[key]
        )

        # Completed correct file already exists.
        # Every multipart for it is garbage.
        if completed_exact:

            for upload, raw_key, size, count in items:

                print(
                    f"ABORT   {gib(size):6.2f} GiB "
                    f"{count:4d} parts  {key}"
                )

                abort.append(
                    (raw_key, upload["UploadId"], size)
                )

            continue

        # Missing expected model:
        # preserve newest multipart and remove duplicates.
        if key in EXPECTED:

            newest = items[0]

            upload, raw_key, size, count = newest

            print(
                f"RESUME  {gib(size):6.2f} GiB "
                f"{count:4d} parts  {key}"
            )

            for upload, raw_key, size, count in items[1:]:

                print(
                    f"ABORT   {gib(size):6.2f} GiB "
                    f"{count:4d} parts duplicate  {key}"
                )

                abort.append(
                    (raw_key, upload["UploadId"], size)
                )

        else:

            for upload, raw_key, size, count in items:

                print(
                    f"UNKNOWN {gib(size):6.2f} GiB "
                    f"{count:4d} parts  {raw_key}"
                )

    free_bytes = sum(x[2] for x in abort)

    print("\nSTORAGE")

    print(
        f"Completed:       {gib(completed_bytes):.2f} GiB"
    )

    print(
        f"Multipart:       {gib(multipart_bytes):.2f} GiB"
    )

    print(
        f"Current approx:  "
        f"{gib(completed_bytes + multipart_bytes):.2f} GiB"
    )

    print(
        f"Will free:       {gib(free_bytes):.2f} GiB"
    )

    print(
        f"After cleanup:   "
        f"{gib(completed_bytes + multipart_bytes - free_bytes):.2f} GiB"
    )

    if not args.apply:

        print("\nDRY RUN — NOTHING DELETED")
        print("Run with --apply after checking ABORT/RESUME.")
        return

    print("\nCLEANING")

    for raw_key, upload_id, size in abort:

        print(
            f"Deleting multipart {gib(size):.2f} GiB:",
            raw_key,
        )

        s3.abort_multipart_upload(
            Bucket=bucket,
            Key=raw_key,
            UploadId=upload_id,
        )

    print("\nDONE")
    print("Completed files preserved.")
    print("Newest resumable multipart uploads preserved.")


if __name__ == "__main__":
    main()
