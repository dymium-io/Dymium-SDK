"""Shared detected-entity normalization helpers."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from dymium.types import DetectedEntity

_NON_TYPE_CHARS = re.compile(r"[^A-Za-z0-9]+")
_MULTI_UNDERSCORE = re.compile(r"_+")


def canonical_entity_type(value: Any) -> str:
    raw = str(value or "VALUE").strip().upper()
    raw = _NON_TYPE_CHARS.sub("_", raw)
    raw = _MULTI_UNDERSCORE.sub("_", raw).strip("_")
    return raw or "VALUE"


def make_detected_entity(
    *,
    entity_type: Any,
    score: Any = None,
    begin_offset: Any = None,
    end_offset: Any = None,
    text: Any = None,
    source: str | None = None,
) -> Dict[str, Any]:
    canonical = canonical_entity_type(entity_type)
    provider_type = str(entity_type) if entity_type is not None else None

    start = _as_int(begin_offset)
    end = _as_int(end_offset)
    if start is not None and end is not None and end < start:
        start = None
        end = None

    snippet = str(text) if text is not None else None
    model = DetectedEntity(
        type=canonical,
        providerType=provider_type,
        source=source,
        score=_as_float(score),
        beginOffset=start,
        endOffset=end,
        text=snippet,
    )
    return model.model_dump(exclude_none=True)


def normalize_detected_entities(
    entities: Iterable[Any],
    *,
    source: str | None = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw_entity in entities:
        entity = raw_entity
        if hasattr(entity, "model_dump"):
            entity = entity.model_dump()
        if not isinstance(entity, dict):
            continue

        entity_type = entity.get("type")
        if entity_type is None:
            entity_type = entity.get("entity_type")
        if entity_type is None:
            entity_type = entity.get("entity")
        if entity_type is None:
            entity_type = entity.get("Type")
        if entity_type is None:
            entity_type = entity.get("category")

        begin = entity.get("beginOffset", entity.get("start"))
        if begin is None:
            begin = entity.get("BeginOffset", entity.get("offset"))
        end = entity.get("endOffset", entity.get("end"))
        if end is None:
            end = entity.get("EndOffset")
        if end is None and begin is not None:
            length = entity.get("length")
            length_int = _as_int(length)
            begin_int = _as_int(begin)
            if length_int is not None and begin_int is not None:
                end = begin_int + length_int

        text = entity.get("text", entity.get("quote"))
        score = entity.get("score", entity.get("Score"))
        if score is None:
            score = entity.get("confidenceScore", entity.get("likelihood"))

        out.append(
            make_detected_entity(
                entity_type=entity_type,
                score=score,
                begin_offset=begin,
                end_offset=end,
                text=text,
                source=source or entity.get("source"),
            )
        )
    return out


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return None
    try:
        text = str(value).strip()
        if text == "":
            return None
        return int(text)
    except Exception:
        return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        token = value.strip().upper()
        # Preserve non-numeric likelihood enums by omitting score.
        if token in {"VERY_UNLIKELY", "UNLIKELY", "POSSIBLE", "LIKELY", "VERY_LIKELY"}:
            return None
    try:
        return float(value)
    except Exception:
        return None
