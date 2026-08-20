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
        raise RuntimeError(
            f"RunPod REST {method} {path} failed: HTTP {response.status_code}: {response.text[:4000]}"
        )
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"RunPod REST {method} {path} returned a non-object response")
    return data


def get_template_id(endpoint: dict[str, Any]) -> str:
    template_id = endpoint.get("templateId")
    if isinstance(template_id, str) and template_id:
        return template_id

    template = endpoint.get("template")
    if isinstance(template, dict):
        nested_id = template.get("id")
        if isinstance(nested_id, str) and nested_id:
            return nested_id

    raise RuntimeError("RunPod endpoint response did not include templateId or template.id")


def main() -> None:
    token = need("RUNPOD_API_KEY")
    endpoint_id = need("RUNPOD_ENDPOINT_ID")
    image_name = need("RUNPOD_IMAGE_NAME")

    endpoint = request("GET", f"/endpoints/{endpoint_id}", token=token)
    template_id = get_template_id(endpoint)

    template = request("GET", f"/templates/{template_id}", token=token)
    if template.get("id") != template_id:
        raise RuntimeError(f"RunPod returned unexpected template id: {template.get('id')!r}")

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

    updated = request(
        "POST",
        f"/templates/{template_id}/update",
        token=token,
        json=payload,
    )

    if updated.get("imageName") != image_name:
        raise RuntimeError(
            f"RunPod template update returned unexpected imageName: {updated.get('imageName')!r}"
        )

    confirmed = request("GET", f"/endpoints/{endpoint_id}", token=token)
    confirmed_template_id = get_template_id(confirmed)
    if confirmed_template_id != template_id:
        raise RuntimeError(
            f"Endpoint template changed unexpectedly during deployment: "
            f"{confirmed_template_id!r} != {template_id!r}"
        )

    print(f"DEPLOYED endpoint={endpoint_id} template={template_id} image={image_name}")


if __name__ == "__main__":
    main()
