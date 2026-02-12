"""Google Cloud DLP detector."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import requests

from .regex import detect_regex_entities

class GoogleDLPDetector:
    def __init__(
        self,
        project_id: str,
        credentials: Dict[str, str] | None = None,
        base_url: str = "https://dlp.googleapis.com",
        timeout_s: int = 10,
        regex_rules: Optional[list[Dict[str, Any]]] = None,
    ) -> None:
        self.project_id = project_id
        self.credentials = credentials or {}
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.regex_rules = regex_rules or []

    def detect(self, text: str, options: Optional[Dict[str, Any]] = None) -> Iterable[Dict[str, Any]]:
        if not text:
            return []
        options = options or {}
        info_types = options.get("info_types") or options.get("infoTypes")
        min_likelihood = options.get("min_likelihood") or options.get("minLikelihood")

        inspect_config: Dict[str, Any] = {"includeQuote": True}
        if info_types:
            inspect_config["infoTypes"] = [{"name": t} for t in info_types]
        if min_likelihood:
            inspect_config["minLikelihood"] = min_likelihood

        payload = {
            "item": {"value": text},
            "inspectConfig": inspect_config,
        }

        url = f"{self.base_url}/v2/projects/{self.project_id}/content:inspect"
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        api_key = self.credentials.get("api_key") or self.credentials.get("apiKey")
        token = self.credentials.get("token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if api_key:
            url = f"{url}?key={api_key}"

        resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout_s)
        resp.raise_for_status()
        data = resp.json()
        findings = (data.get("result") or {}).get("findings") or []

        out = []
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            info_type = (finding.get("infoType") or {}).get("name")
            quote = finding.get("quote")
            location = finding.get("location") or {}
            byte_range = location.get("byteRange") or {}
            start = byte_range.get("start")
            end = byte_range.get("end")
            out.append({
                "type": info_type,
                "score": finding.get("likelihood"),
                "beginOffset": start,
                "endOffset": end,
                "text": quote,
            })
        out.extend(detect_regex_entities(text, self.regex_rules))
        return out

    def normalize(self, entities: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
        return entities
