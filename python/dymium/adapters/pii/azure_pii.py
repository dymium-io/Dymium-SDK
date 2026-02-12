"""Azure PII detector (Text Analytics)."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import requests

from .common import make_detected_entity, normalize_detected_entities
from .regex import detect_regex_entities

class AzurePIIDetector:
    def __init__(
        self,
        endpoint: str,
        api_key: str | None = None,
        bearer_token: str | None = None,
        timeout_s: int = 10,
        api_version: str = "2022-05-01",
        use_legacy_endpoint: bool = False,
        regex_rules: Optional[list[Dict[str, Any]]] = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.bearer_token = bearer_token
        self.timeout_s = timeout_s
        self.api_version = api_version
        self.use_legacy_endpoint = use_legacy_endpoint
        self.regex_rules = regex_rules or []

    def detect(self, text: str, options: Optional[Dict[str, Any]] = None) -> Iterable[Dict[str, Any]]:
        if not text:
            return []
        options = options or {}
        language = options.get("language", "en")

        if self.use_legacy_endpoint:
            url = f"{self.endpoint}/text/analytics/v3.1/entities/recognition/pii"
            payload = {
                "documents": [
                    {"id": "0", "text": text, "language": language},
                ]
            }
        else:
            url = f"{self.endpoint}/language/:analyze-text?api-version={self.api_version}"
            params = _azure_params(options)
            payload = {
                "kind": "PiiEntityRecognition",
                "analysisInput": {
                    "documents": [
                        {"id": "0", "text": text, "language": language},
                    ]
                },
            }
            if params:
                payload["parameters"] = params

        resp = requests.post(
            url,
            json=payload,
            headers=self._headers(),
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()

        documents = _extract_documents(data)
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
            out.append(
                make_detected_entity(
                    entity_type=ent.get("category"),
                    score=ent.get("confidenceScore", 1.0),
                    begin_offset=start,
                    end_offset=end,
                    text=ent.get("text"),
                    source="azure_pii",
                )
            )
        out.extend(detect_regex_entities(text, self.regex_rules))
        return out

    def normalize(self, entities: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
        return normalize_detected_entities(entities, source="azure_pii")

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if not self.api_key and not self.bearer_token:
            raise ValueError("AzurePIIDetector requires api_key or bearer_token")
        if self.api_key:
            headers["Ocp-Apim-Subscription-Key"] = self.api_key
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers


def _azure_params(options: Dict[str, Any]) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    for key in (
        "domain",
        "piiCategories",
        "excludePiiCategories",
        "modelVersion",
        "confidenceScoreThreshold",
        "stringIndexType",
        "loggingOptOut",
        "disableEntityValidation",
        "entitySynonyms",
        "valueExclusionPolicy",
        "redactionPolicies",
    ):
        if key in options and options[key] is not None:
            params[key] = options[key]
        snake = _camel_to_snake(key)
        if snake in options and options[snake] is not None:
            params[key] = options[snake]
    return params


def _camel_to_snake(text: str) -> str:
    out = []
    for ch in text:
        if ch.isupper():
            out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out).lstrip("_")


def _extract_documents(payload: Dict[str, Any]) -> list[Dict[str, Any]]:
    # Legacy Text Analytics shape.
    docs = payload.get("documents")
    if isinstance(docs, list):
        return [d for d in docs if isinstance(d, dict)]

    # Analyze Text shape.
    results = payload.get("results")
    if isinstance(results, dict):
        docs = results.get("documents")
        if isinstance(docs, list):
            return [d for d in docs if isinstance(d, dict)]
    return []
