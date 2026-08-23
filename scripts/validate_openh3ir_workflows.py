#!/usr/bin/env python3
"""Static A/B controls for the raw and OpenH3-IR API-format workflows."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "workflows/minimax_h3_i2v_raw.api.json"
COMPILED = ROOT / "workflows/minimax_h3_i2v_openh3ir.api.json"
PROMPT = (
    "The two children keep running quickly through deep snow. "
    "Their movement is natural and energetic. "
    "The camera follows closely behind them at low height."
)


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict) and value, f"{path} is not an API workflow object"
    for node_id, node in value.items():
        assert isinstance(node, dict) and "class_type" in node and "inputs" in node, (
            f"{path}: malformed node {node_id}"
        )
    return value


def main() -> None:
    raw = load(RAW)
    compiled = load(COMPILED)

    assert raw["105:104"]["inputs"]["prompt"] == PROMPT
    assert compiled["202"]["inputs"]["intent"] == PROMPT
    assert raw["105:15"]["inputs"]["noise_seed"] == 123
    assert compiled["105:15"]["inputs"]["noise_seed"] == 123
    assert raw["105:9"]["inputs"]["steps"] == compiled["105:9"]["inputs"]["steps"] == 20
    assert raw["105:9"]["inputs"]["scheduler"] == compiled["105:9"]["inputs"]["scheduler"]
    assert raw["105:111"]["inputs"]["value"] == compiled["202"]["inputs"]["seconds"] == 5
    assert raw["115"]["inputs"]["megapixels"] == compiled["202"]["inputs"]["megapixels"] == 0.4
    assert raw["105:6"]["inputs"]["unet_name"] == compiled["200"]["inputs"]["frames_model"]
    assert compiled["202"]["inputs"]["creativity"] == "balanced"
    assert compiled["202"]["inputs"]["shots"] == "1"
    assert compiled["200"]["inputs"]["timeout_s"] == 180
    assert compiled["200"]["inputs"]["llm_url"] == ""
    assert compiled["200"]["inputs"]["llm_model"] == ""
    assert compiled["207"]["class_type"] == "LoadImage"
    assert compiled["207"]["inputs"]["image"] == "openh3ir_test/input.png"
    assert "207" not in {
        str(value[0])
        for node in compiled.values()
        for value in node["inputs"].values()
        if isinstance(value, list) and len(value) == 2
    }, "the staging marker must not alter the OpenH3/H3 execution graph"
    assert any(node["class_type"] == "OpenH3IRMedia" for node in compiled.values())
    assert any(node["class_type"] == "OpenH3IRCompile" for node in compiled.values())
    assert "H3IR_LLM_API_KEY" not in json.dumps(compiled)
    print("OpenH3-IR A/B workflow controls: PASS")


if __name__ == "__main__":
    main()
