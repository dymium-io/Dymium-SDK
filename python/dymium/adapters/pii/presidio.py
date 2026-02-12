"""Presidio detector (HTTP service).

Expected endpoint:
- POST {base_url}/analyze
  Payload: {
    "text": "...",
    "language": "en",
    "entities": ["EMAIL_ADDRESS", ...],
    "score_threshold": 0.35
  }
Response (Presidio standard):
- [{ "entity_type": "EMAIL_ADDRESS", "start": 10, "end": 25, "score": 0.98 }, ...]
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import requests

from .common import make_detected_entity, normalize_detected_entities
from .regex import detect_regex_entities


class PresidioDetector:
    def __init__(
        self,
        base_url: str,
        timeout_s: int = 10,
        regex_rules: Optional[list[Dict[str, Any]]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.regex_rules = regex_rules or []
        self.calls: list[Dict[str, Any]] = []
        self.last_request: Dict[str, Any] | None = None
        self.last_response: Any = None

    def detect(self, text: str, options: Optional[Dict[str, Any]] = None) -> Iterable[Dict[str, Any]]:
        if not text:
            return []
        options = options or {}
        payload: Dict[str, Any] = {
            "text": text,
            "language": options.get("language", "en"),
        }
        if "entities" in options:
            payload["entities"] = options.get("entities")
        if "score_threshold" in options:
            payload["score_threshold"] = options.get("score_threshold")

        url = f"{self.base_url}/analyze"
        self.last_request = {"url": url, "payload": payload}
        resp = requests.post(url, json=payload, timeout=self.timeout_s)
        resp.raise_for_status()
        data = resp.json()
        self.last_response = data
        self.calls.append({
            "request": self.last_request,
            "response": data,
        })
        if not isinstance(data, list):
            data = []

        entities = [self._normalize_entity(e, text) for e in data if isinstance(e, dict)]
        entities.extend(detect_regex_entities(text, self.regex_rules))
        return entities

    def normalize(self, entities: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
        return normalize_detected_entities(entities, source="presidio")

    @staticmethod
    def _normalize_entity(entity: Dict[str, Any], text: str) -> Dict[str, Any]:
        etype = entity.get("entity_type") or entity.get("type") or entity.get("entity")
        score = entity.get("score", 1.0)
        start = entity.get("start")
        end = entity.get("end")

        snippet = None
        if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(text):
            snippet = text[start:end]

        return make_detected_entity(
            entity_type=etype,
            score=score,
            begin_offset=start,
            end_offset=end,
            text=snippet,
            source="presidio",
        )
