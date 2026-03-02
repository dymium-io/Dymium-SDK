#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from typing import Any, Dict

from dymium.detectors.pii import HuggingFacePIIDetector
from dymium.redaction import RedactionEngine
from dymium.registry import GLOBAL_REGISTRY, register_default_adapters
from dymium.sanitization import Sanitizer, SanitizationContext


def _parse_threshold(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except Exception:
        raise ValueError(f"Invalid HF_SCORE_THRESHOLD: {value!r}")


def _print_entities(label: str, entities: list[Dict[str, Any]]) -> None:
    print(f"{label} ({len(entities)}):")
    for ent in entities[:10]:
        etype = ent.get("type")
        text = ent.get("text")
        score = ent.get("score")
        start = ent.get("beginOffset")
        end = ent.get("endOffset")
        print(f"  - type={etype} score={score} span=({start},{end}) text={text!r}")
    if len(entities) > 10:
        print(f"  ... {len(entities) - 10} more")


def _assert_detected(name: str, entities: list[Dict[str, Any]]) -> None:
    if not entities:
        print(f"FAIL: {name} returned no entities.", file=sys.stderr)
        sys.exit(1)
    bad = [e for e in entities if not isinstance(e.get("beginOffset"), int) or not isinstance(e.get("endOffset"), int)]
    if bad:
        print(f"FAIL: {name} produced entities without usable offsets.", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    model_id = os.getenv("HF_MODEL_ID", "dymium/Dymium-NER-v1")
    text = os.getenv(
        "HF_TEST_TEXT",
        "My name is Alice Johnson. Email alice@example.com or call 415-555-0135.",
    )
    threshold = _parse_threshold(os.getenv("HF_SCORE_THRESHOLD"))

    print(f"Model: {model_id}")
    if threshold is not None:
        print(f"Score threshold: {threshold}")

    detector = HuggingFacePIIDetector(
        model_id=model_id,
        aggregation_strategy="simple",
        score_threshold=threshold,
    )
    raw_entities = list(detector.detect(text))
    entities = list(detector.normalize(raw_entities))
    _print_entities("Direct detector entities", entities)
    _assert_detected("HuggingFacePIIDetector", entities)

    sanitizer = Sanitizer(pii=detector, redaction=RedactionEngine())
    ctx = SanitizationContext()
    safe_text = sanitizer.sanitize_text(text, ctx)
    print("\nSanitized text:")
    print(safe_text)
    print("\nPlaceholder map:")
    print(ctx.placeholder_map)
    print("\nSecurity summary:")
    print(ctx.security_summary)

    if not ctx.placeholder_map:
        print("FAIL: sanitizer produced an empty placeholder map.", file=sys.stderr)
        sys.exit(1)
    if "PH_" not in safe_text:
        print("FAIL: sanitized text does not contain placeholders.", file=sys.stderr)
        sys.exit(1)

    register_default_adapters()
    cfg: Dict[str, Any] = {"model_id": model_id}
    if threshold is not None:
        cfg["score_threshold"] = threshold
    via_registry = GLOBAL_REGISTRY.create("pii", "huggingface", cfg)
    reg_entities = list(via_registry.normalize(via_registry.detect(text)))
    _print_entities("\nRegistry detector entities", reg_entities)
    _assert_detected("registry[huggingface]", reg_entities)

    print("\nPASS: HuggingFacePIIDetector smoke test passed.")


if __name__ == "__main__":
    main()
