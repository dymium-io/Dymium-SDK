"""Hugging Face Transformers PII detector."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from .common import make_detected_entity, normalize_detected_entities
from .regex import detect_regex_entities


class HuggingFacePIIDetector:
    """Local Hugging Face token-classification detector for PII."""

    def __init__(
        self,
        model_id: str = "dymium/Dymium-NER-v1",
        score_threshold: float | None = None,
        device: int | str | None = None,
        tokenizer: str | None = None,
        pipeline_kwargs: Optional[Dict[str, Any]] = None,
        regex_rules: Optional[list[Dict[str, Any]]] = None,
    ) -> None:
        self.model_id = model_id
        self.score_threshold = score_threshold
        self.device = device
        self.tokenizer = tokenizer
        self.pipeline_kwargs = pipeline_kwargs or {}
        self.regex_rules = regex_rules or []
        self._pipeline: Any | None = None

    def detect(self, text: str) -> Iterable[Dict[str, Any]]:
        if not text:
            return []

        ner = self._get_pipeline()
        raw = ner(text)
        if not isinstance(raw, list):
            raw = []

        out: list[Dict[str, Any]] = []
        for ent in raw:
            if not isinstance(ent, dict):
                continue
            score = _as_float(ent.get("score"))
            if self.score_threshold is not None and score is not None and score < self.score_threshold:
                continue

            label = ent.get("entity_group") or ent.get("entity") or ent.get("label")
            start = _as_int(ent.get("start"))
            end = _as_int(ent.get("end"))
            snippet = _entity_text(text, start, end, fallback=ent.get("word"))

            out.append(
                make_detected_entity(
                    entity_type=label,
                    score=score,
                    begin_offset=start,
                    end_offset=end,
                    text=snippet,
                    source="huggingface",
                )
            )

        out.extend(detect_regex_entities(text, self.regex_rules))
        return out

    def normalize(self, entities: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
        return normalize_detected_entities(entities, source="huggingface")

    def _get_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        try:
            from transformers import pipeline  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "transformers is required for HuggingFacePIIDetector. "
                "Install optional dependencies with: pip install 'dymium[hf]' "
                "or install transformers and torch directly."
            ) from exc

        kwargs: Dict[str, Any] = {
            "task": "token-classification",
            "model": self.model_id,
            # We currently standardize entity grouping behavior across the SDK.
            "aggregation_strategy": "simple",
        }
        if self.tokenizer:
            kwargs["tokenizer"] = self.tokenizer
        if self.device is not None:
            kwargs["device"] = self.device
        kwargs.update(self.pipeline_kwargs)

        self._pipeline = pipeline(**kwargs)
        return self._pipeline


def _entity_text(text: str, start: int | None, end: int | None, fallback: Any = None) -> str | None:
    if isinstance(start, int) and isinstance(end, int):
        if 0 <= start <= end <= len(text):
            return text[start:end]
    if isinstance(fallback, str):
        return fallback
    return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    try:
        text = str(value).strip()
        if text == "":
            return None
        return int(text)
    except Exception:
        return None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except Exception:
        return None
