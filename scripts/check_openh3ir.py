#!/usr/bin/env python3
"""Fail-loud OpenH3-IR installation and vision-endpoint health check."""

from __future__ import annotations

import importlib.metadata
import ast
import os
import re
import shutil
import subprocess
from urllib.parse import urlsplit, urlunsplit


BOOL_FIELDS = ("health", "chat_ok", "vision_ok")


def _safe_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except Exception:
        return "(configured; could not display safely)"


def _field(text: str, name: str) -> str:
    match = re.search(rf"(?m)^\s*{re.escape(name)}:\s*(.*?)\s*$", text)
    return match.group(1).strip() if match else ""


def _yes(value: str) -> bool:
    return value.lower() == "true"


def main() -> int:
    try:
        version = importlib.metadata.version("open-h3-ir")
        import h3ir.compile  # noqa: F401
    except Exception as exc:
        print("OpenH3-IR installed: NO")
        print(f"Reason: {type(exc).__name__}: {exc}")
        return 2

    print("OpenH3-IR installed: YES")
    print(f"OpenH3-IR version: {version}")

    url = os.environ.get("H3IR_LLM_URL", "").strip()
    model = os.environ.get("H3IR_LLM_MODEL", "").strip()
    if not url or not model:
        print("LLM reachable: NO")
        print(f"Model selected: {model or '(missing)'}")
        print("Vision: NO")
        print("OpenH3-IR vision LLM endpoint is not configured. Set H3IR_LLM_URL and H3IR_LLM_MODEL.")
        return 3

    child_env = os.environ.copy()
    api_key = child_env.get("H3IR_LLM_API_KEY", "").strip()
    if api_key and not child_env.get("H3IR_LLM_KEY", "").strip():
        child_env["H3IR_LLM_KEY"] = api_key
    timeout = int(child_env.get("H3IR_TIMEOUT_SECONDS", "180"))
    child_env.setdefault("H3IR_LLM_TIMEOUT", str(timeout))

    print(f"Endpoint: {_safe_url(url)}")
    print(f"Model selected: {model}")
    command = shutil.which("h3ir")
    if not command:
        print("LLM reachable: NO")
        print("Vision: NO")
        print("The h3ir console command is not installed on PATH.")
        return 4
    try:
        result = subprocess.run(
            [command, "doctor"],
            capture_output=True,
            text=True,
            env=child_env,
            timeout=timeout + 30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("LLM reachable: NO")
        print("Vision: NO")
        print(f"Doctor timed out after {timeout + 30} seconds.")
        return 4

    doctor = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    values = {name: _field(doctor, name) for name in BOOL_FIELDS}
    ids_text = _field(doctor, "model_ids")
    try:
        ids = ast.literal_eval(ids_text) if ids_text else []
    except (SyntaxError, ValueError):
        ids = []
    served = isinstance(ids, list) and model in ids

    print(f"LLM reachable: {'YES' if _yes(values['health']) else 'NO'}")
    print(f"Chat completion: {'YES' if _yes(values['chat_ok']) else 'NO'}")
    print(f"Model exists: {'YES' if served else 'NO'}")
    print(f"Vision: {'YES' if _yes(values['vision_ok']) else 'NO'}")

    ok = all(_yes(values[name]) for name in BOOL_FIELDS) and served
    if not ok:
        for key in ("health_via", "health_tried", "models_error", "model_warning", "chat_error", "vision_error", "vision_note"):
            value = _field(doctor, key)
            if value:
                print(f"{key}: {value}")
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
