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


def request(
    method: str,
    path: str,
    *,
    token: str,
    json: dict[str, Any] | None = None,
) -> Any:
    response = requests.request(
        method,
        REST + path,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=json,
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(
            f"RunPod REST {method} {path} failed: "
            f"HTTP {response.status_code}: {response.text[:4000]}"
        )
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def get_template_id(endpoint: dict[str, Any]) -> str:
    template_id = endpoint.get("templateId")
    if isinstance(template_id, str) and template_id:
        return template_id

    template = endpoint.get("template")
    if isinstance(template, dict):
        nested_id = template.get("id")
        if isinstance(nested_id, str) and nested_id:
            return nested_id

    raise RuntimeError(
        "RunPod endpoint response did not include templateId or template.id"
    )


def get_bound_template(token: str, template_id: str) -> dict[str, Any]:
    templates = request(
        "GET",
        "/templates?includeEndpointBoundTemplates=true",
        token=token,
    )
    if not isinstance(templates, list):
        raise RuntimeError(
            "RunPod template list returned an unexpected non-list response"
        )

    for template in templates:
        if isinstance(template, dict) and template.get("id") == template_id:
            return template

    raise RuntimeError(
        f"Template {template_id} is bound to the endpoint but was not returned "
        "by GET /templates?includeEndpointBoundTemplates=true"
    )


def main() -> None:
    token = need("RUNPOD_API_KEY")
    endpoint_id = need("RUNPOD_ENDPOINT_ID")
    image_name = need("RUNPOD_IMAGE_NAME")

    # Read the existing endpoint. Do not create or recreate it.
    endpoint = request("GET", f"/endpoints/{endpoint_id}", token=token)
    if not isinstance(endpoint, dict):
        raise RuntimeError(
            "RunPod endpoint lookup returned an unexpected response"
        )

    template_id = get_template_id(endpoint)
    template = get_bound_template(token, template_id)

    # Preserve the current template configuration and change only imageName.
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
    if not isinstance(updated, dict):
        raise RuntimeError(
            "RunPod template update returned an unexpected response"
        )

    if updated.get("imageName") != image_name:
        raise RuntimeError(
            "RunPod template update returned unexpected imageName: "
            f"{updated.get('imageName')!r}"
        )

    # Verify the same endpoint remains bound to the same template.
    confirmed = request("GET", f"/endpoints/{endpoint_id}", token=token)
    if not isinstance(confirmed, dict):
        raise RuntimeError(
            "RunPod endpoint verification returned an unexpected response"
        )

    confirmed_template_id = get_template_id(confirmed)
    if confirmed_template_id != template_id:
        raise RuntimeError(
            "Endpoint template changed unexpectedly during deployment: "
            f"{confirmed_template_id!r} != {template_id!r}"
        )

    print(
        f"DEPLOYED endpoint={endpoint_id} "
        f"template={template_id} image={image_name}"
    )


if __name__ == "__main__":
    main()
