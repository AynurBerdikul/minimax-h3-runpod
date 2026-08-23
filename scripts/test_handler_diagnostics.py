#!/usr/bin/env python3
"""CPU-only control for tagged prompt/metadata artifacts in the RunPod handler."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        os.environ["RUNPOD_VOLUME_ROOT"] = temp
        sys.modules["runpod"] = types.SimpleNamespace(
            serverless=types.SimpleNamespace(start=lambda _: None)
        )
        path = Path(__file__).resolve().parents[1] / "remote/handler-h3.py"
        spec = importlib.util.spec_from_file_location("handler_h3_test", path)
        assert spec and spec.loader
        handler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(handler)

        workflow = {
            "1": {"class_type": "PreviewAny", "inputs": {}, "_meta": {
                "title": "OPENH3IR_ARTIFACT:raw_prompt.txt"}},
            "2": {"class_type": "PreviewAny", "inputs": {}, "_meta": {
                "title": "OPENH3IR_ARTIFACT:compiled_prompt.txt"}},
            "3": {"class_type": "PreviewAny", "inputs": {}, "_meta": {
                "title": "OPENH3IR_ARTIFACT:generation_metadata.json"}},
        }
        history = {"outputs": {
            "1": {"text": ["raw"]},
            "2": {"text": ["compiled"]},
            "3": {"text": [json.dumps({
                "raw_prompt": "raw", "compiled_prompt": "", "timestamp": ""
            })]},
        }}
        artifacts = handler._export_tagged_text_artifacts(history, workflow, "job")
        assert len(artifacts) == 3
        metadata = json.loads(
            (Path(handler.RESULT_ROOT) / "job/generation_metadata.json").read_text(encoding="utf-8")
        )
        assert metadata["compiled_prompt"] == "compiled"
        assert metadata["timestamp"]
        assert all(item["key"].startswith("runpod-results/job/") for item in artifacts)
    print("Handler tagged diagnostic artifacts: PASS")


if __name__ == "__main__":
    main()
