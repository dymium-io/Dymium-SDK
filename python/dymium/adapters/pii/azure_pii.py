"""Azure PII detector (Text Analytics)."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import requests

from .regex import detect_regex_entities

class AzurePIIDetector:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        timeout_s: int = 10,
        regex_rules: Optional[list[Dict[str, Any]]] = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.regex_rules = regex_rules or []

    def detect(self, text: str, options: Optional[Dict[str, Any]] = None) -> Iterable[Dict[str, Any]]:
        if not text:
            return []
        options = options or {}
        language = options.get("language", "en")
        url = f"{self.endpoint}/text/analytics/v3.1/entities/recognition/pii"
        payload = {
            "documents": [
                {"id": "0", "text": text, "language": language},
            ]
        }
        resp = requests.post(
            url,
            json=payload,
            headers={
                "Ocp-Apim-Subscription-Key": self.api_key,
                "Content-Type": "application/json",
            },
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        documents = data.get("documents") or []
        if not documents:
            return []
        entities = documents[0].get("entities") or []
        out = []
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            start = ent.get("offset")
            length = ent.get("length")
            if isinstance(start, int) and isinstance(length, int):
                end = start + length
            else:
                end = None
            out.append({
                "type": ent.get("category"),
                "score": ent.get("confidenceScore", 1.0),
                "beginOffset": start,
                "endOffset": end,
                "text": ent.get("text"),
            })
        out.extend(detect_regex_entities(text, self.regex_rules))
        return out

    def normalize(self, entities: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
        return entities
