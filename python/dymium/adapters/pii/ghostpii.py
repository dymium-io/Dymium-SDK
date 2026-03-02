"""GhostPII detector (Dymium Detect cloud service).

Expected endpoint:
- POST {base_url}/v1/detect/pii
  Payload: {
    "text": "...",
    "entity_types": ["ADDRESS", "AUTH", ...]  # optional; when omitted, server defaults apply
  }

Auth:
- Authorization: Bearer <api_key> (required)

Response formats handled:
- { "entities": [ ... ] }
- [ ... ]  (direct array)
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import requests

from .common import make_detected_entity, normalize_detected_entities
from .regex import detect_regex_entities

class GhostPIIDetector:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_s: int = 10,
        entity_types: Optional[list[str]] = None,
        endpoint_path: str = "/v1/detect/pii",
        language: str = "en",  # legacy, kept for compatibility
        user_patterns: Optional[list[Dict[str, Any]]] = None,  # legacy, kept for compatibility
        regex_rules: Optional[list[Dict[str, Any]]] = None,
    ) -> None:
        if not base_url:
            raise ValueError("GhostPIIDetector requires base_url")
        if not api_key:
            raise ValueError("GhostPIIDetector requires api_key")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.entity_types = self._normalize_entity_types(entity_types)
        self.endpoint_path = endpoint_path
        self.language = language
        self.user_patterns = user_patterns
        self.regex_rules = regex_rules or []

    def detect(self, text: str) -> Iterable[Dict[str, Any]]:
        if not text:
            return []
        payload = {
            "text": text,
        }
        if self.entity_types:
            payload["entity_types"] = list(self.entity_types)

        headers: Dict[str, str] = {"Content-Type": "application/json"}
        headers["Authorization"] = f"Bearer {self.api_key}"

        url = self._endpoint_url()
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
        return normalize_detected_entities(entities, source="ghostpii")

    def _endpoint_url(self) -> str:
        if self.base_url.endswith("/v1/detect/pii"):
            return self.base_url
        if self.base_url.endswith("/v1/detect/pii/"):
            return self.base_url[:-1]
        path = self.endpoint_path or "/v1/detect/pii"
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    @staticmethod
    def _normalize_entity_types(value: Any) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("GhostPIIDetector.entity_types must be a list of strings")
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            token = item.strip().upper()
            if token and token not in out:
                out.append(token)
        return out or None

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

        return make_detected_entity(
            entity_type=etype,
            score=score,
            begin_offset=start,
            end_offset=end,
            text=snippet,
            source="ghostpii",
        )
