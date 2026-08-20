from __future__ import annotations

import os
from typing import Any

import requests

REST = "https://rest.runpod.io/v1"


def need(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing environment variable: {name}")
    return value


def request(method: str, path: str, *, token: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.request(
        method,
        REST + path,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=json,
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(f"RunPod REST {method} {path} failed: HTTP {response.status_code}: {response.text[:4000]}")
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"RunPod REST {method} {path} returned a non-object response")
    return data


def main() -> None:
    token = need("RUNPOD_API_KEY")
    endpoint_id = need("RUNPOD_ENDPOINT_ID")
    image_name = need("RUNPOD_IMAGE_NAME")

    endpoint = request("GET", f"/endpoints/{endpoint_id}", token=token)
    template = endpoint.get("template")
    if not isinstance(template, dict) or not template.get("id"):
        raise RuntimeError("RunPod endpoint response did not include its bound template")

    # Preserve the current template exactly where the API exposes the field.
    # Changing imageName on the bound template triggers RunPod's rolling release;
    # endpoint GPU/volume/scaler settings are not recreated or replaced.
    allowed = (
        "containerDiskInGb",
        "containerRegistryAuthId",
        "dockerEntrypoint",
        "dockerStartCmd",
        "env",
        "isPublic",
        "name",
        "ports",
        "readme",
        "volumeInGb",
        "volumeMountPath",
    )
    payload: dict[str, Any] = {"imageName": image_name}
    for key in allowed:
        if key in template and template[key] is not None:
            payload[key] = template[key]

    updated = request("POST", f"/templates/{template['id']}/update", token=token, json=payload)
    if updated.get("imageName") != image_name:
        raise RuntimeError(
            f"RunPod template update returned unexpected imageName: {updated.get('imageName')!r}"
        )

    # Confirm endpoint still points at the same template after rolling-release trigger.
    confirmed = request("GET", f"/endpoints/{endpoint_id}", token=token)
    confirmed_template = confirmed.get("template") or {}
    if confirmed_template.get("id") != template["id"]:
        raise RuntimeError("Endpoint template changed unexpectedly during deployment")

    print(f"DEPLOYED endpoint={endpoint_id} template={template['id']} image={image_name}")


if __name__ == "__main__":
    main()
