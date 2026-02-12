"""AWS Comprehend PII detector."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from .regex import detect_regex_entities


class ComprehendDetector:
    def __init__(
        self,
        region: str,
        credentials: Dict[str, str] | None = None,
        regex_rules: Optional[list[Dict[str, Any]]] = None,
    ) -> None:
        self.region = region
        self.credentials = credentials or {}
        self.regex_rules = regex_rules or []

    def detect(self, text: str, options: Optional[Dict[str, Any]] = None) -> Iterable[Dict[str, Any]]:
        if not text:
            return []
        options = options or {}
        language_code = options.get("language_code") or options.get("languageCode") or "en"

        try:
            import boto3  # type: ignore
        except Exception as exc:
            raise RuntimeError("boto3 is required for ComprehendDetector") from exc

        client = boto3.client(
            "comprehend",
            region_name=self.region,
            aws_access_key_id=self.credentials.get("access_key_id"),
            aws_secret_access_key=self.credentials.get("secret_access_key"),
            aws_session_token=self.credentials.get("session_token"),
        )
        resp = client.detect_pii_entities(Text=text, LanguageCode=language_code)
        entities = resp.get("Entities") or []
        out = []
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            out.append({
                "type": ent.get("Type"),
                "score": ent.get("Score", 1.0),
                "beginOffset": ent.get("BeginOffset"),
                "endOffset": ent.get("EndOffset"),
            })
        out.extend(detect_regex_entities(text, self.regex_rules))
        return out

    def normalize(self, entities: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
        return entities
