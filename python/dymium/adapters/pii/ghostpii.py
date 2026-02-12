"""GhostPII detector (Dymium-hosted PII service).

Expected endpoint:
- POST {base_url}/analyze
  Payload: { "text": "...", "language": "en", "user_patterns": [...] }

Response formats handled:
- { "entities": [ ... ] }
- [ ... ]  (direct array)
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import requests

from .regex import detect_regex_entities

class GhostPIIDetector:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout_s: int = 10,
        regex_rules: Optional[list[Dict[str, Any]]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.regex_rules = regex_rules or []

    def detect(self, text: str, options: Optional[Dict[str, Any]] = None) -> Iterable[Dict[str, Any]]:
        if not text:
            return []
        options = options or {}
        payload = {
            "text": text,
            "language": options.get("language", "en"),
        }
        if "user_patterns" in options:
            payload["user_patterns"] = options.get("user_patterns")

        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.base_url}/analyze"
        resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout_s)
        resp.raise_for_status()
        data = resp.json()

        entities = data.get("entities", data) if isinstance(data, dict) else data
        if not isinstance(entities, list):
            entities = []
        out = [self._normalize_entity(e, text) for e in entities if isinstance(e, dict)]
        out.extend(detect_regex_entities(text, self.regex_rules))
        return out

    def normalize(self, entities: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
        return entities

    @staticmethod
    def _normalize_entity(entity: Dict[str, Any], text: str) -> Dict[str, Any]:
        etype = entity.get("type") or entity.get("entity_type") or entity.get("entity")
        score = entity.get("score", 1.0)

        start = (
            entity.get("beginOffset")
            if entity.get("beginOffset") is not None
            else entity.get("start")
        )
        end = (
            entity.get("endOffset")
            if entity.get("endOffset") is not None
            else entity.get("end")
        )
        if start is None:
            start = entity.get("start_position")
        if end is None:
            end = entity.get("end_position")

        snippet = entity.get("text")
        if snippet is None and isinstance(start, int) and isinstance(end, int):
            if 0 <= start <= end <= len(text):
                snippet = text[start:end]

        normalized = {
            "type": etype,
            "score": score,
            "beginOffset": start,
            "endOffset": end,
        }
        if snippet is not None:
            normalized["text"] = snippet
        return normalized
