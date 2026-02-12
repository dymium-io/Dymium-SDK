"""AWS Comprehend PII detector."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from .common import make_detected_entity, normalize_detected_entities
from .regex import detect_regex_entities


class ComprehendDetector:
    def __init__(
        self,
        region: str,
        credentials: Dict[str, str] | None = None,
        endpoint_url: str | None = None,
        language_code: str = "en",
        regex_rules: Optional[list[Dict[str, Any]]] = None,
    ) -> None:
        self.region = region
        self.credentials = credentials or {}
        self.endpoint_url = endpoint_url
        self.language_code = language_code
        self.regex_rules = regex_rules or []

    def detect(self, text: str) -> Iterable[Dict[str, Any]]:
        if not text:
            return []

        try:
            import boto3  # type: ignore
        except Exception as exc:
            raise RuntimeError("boto3 is required for ComprehendDetector") from exc

        client = boto3.client(
            "comprehend",
            region_name=self.region,
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.credentials.get("access_key_id"),
            aws_secret_access_key=self.credentials.get("secret_access_key"),
            aws_session_token=self.credentials.get("session_token"),
        )
        resp = client.detect_pii_entities(Text=text, LanguageCode=self.language_code)
        entities = resp.get("Entities") or []
        out = []
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            out.append(
                make_detected_entity(
                    entity_type=ent.get("Type"),
                    score=ent.get("Score", 1.0),
                    begin_offset=ent.get("BeginOffset"),
                    end_offset=ent.get("EndOffset"),
                    source="comprehend",
                )
            )
        out.extend(detect_regex_entities(text, self.regex_rules))
        return out

    def normalize(self, entities: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
        return normalize_detected_entities(entities, source="comprehend")
