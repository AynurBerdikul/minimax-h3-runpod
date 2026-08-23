#!/usr/bin/env python3
"""Build-time regression checks for the pinned OpenH3-IR provider adapter."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

from h3ir.backend import Backend, EndpointRefused


def config() -> SimpleNamespace:
    return SimpleNamespace(
        llm=SimpleNamespace(
            model="vision-model",
            base_url="https://provider.example/v1",
            api_key="",
            timeout_s=5,
            default_thinking=False,
            max_tokens=256,
            guided_decoding=False,
        )
    )


def success() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "vision-model",
            "choices": [{"finish_reason": "stop", "message": {"content": "ready"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )


def test_auto_negotiation_and_cache() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "seed" in body or "chat_template_kwargs" in body:
            return httpx.Response(
                400,
                json={"error": {"message": (
                    'Invalid JSON payload. Unknown name "seed": Cannot find field. '
                    'Unknown name "chat_template_kwargs": Cannot find field.'
                )}},
            )
        return success()

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = Backend(config(), client=client)
    first = backend.chat([{"role": "user", "content": "first"}], seed=7, retries=0)
    second = backend.chat([{"role": "user", "content": "second"}], seed=8, retries=0)
    assert first.content == second.content == "ready"
    assert len(bodies) == 3, bodies
    assert "seed" in bodies[0] and "chat_template_kwargs" in bodies[0]
    assert "seed" not in bodies[1] and "chat_template_kwargs" not in bodies[1]
    assert "seed" not in bodies[2] and "chat_template_kwargs" not in bodies[2]
    assert bodies[1]["messages"] == bodies[0]["messages"]


def test_ambiguous_error_is_not_hidden() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "request rejected"}})

    backend = Backend(config(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        backend.chat([{"role": "user", "content": "fail"}], seed=7, retries=0)
    except EndpointRefused as exc:
        assert exc.status == 400
    else:
        raise AssertionError("an ambiguous provider error must propagate")


def test_required_field_rejection_is_not_hidden() -> None:
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            400, json={"error": {"message": 'Unknown name "messages": Cannot find field.'}}
        )

    backend = Backend(config(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        backend.chat([{"role": "user", "content": "fail"}], retries=0)
    except EndpointRefused:
        pass
    else:
        raise AssertionError("a required-field incompatibility must propagate")
    assert requests == 1


if __name__ == "__main__":
    test_auto_negotiation_and_cache()
    test_ambiguous_error_is_not_hidden()
    test_required_field_rejection_is_not_hidden()
    print("OpenH3-IR provider compatibility checks: PASS")
