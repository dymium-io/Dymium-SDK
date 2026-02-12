"""Google Cloud DLP detector."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import requests

from .common import make_detected_entity, normalize_detected_entities
from .regex import detect_regex_entities

class GoogleDLPDetector:
    def __init__(
        self,
        project_id: str,
        credentials: Dict[str, str] | None = None,
        location_id: str | None = None,
        base_url: str = "https://dlp.googleapis.com",
        timeout_s: int = 10,
        regex_rules: Optional[list[Dict[str, Any]]] = None,
    ) -> None:
        self.project_id = project_id
        self.credentials = credentials or {}
        self.location_id = location_id
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.regex_rules = regex_rules or []

    def detect(self, text: str, options: Optional[Dict[str, Any]] = None) -> Iterable[Dict[str, Any]]:
        if not text:
            return []
        options = options or {}
        info_types = options.get("info_types") or options.get("infoTypes")
        min_likelihood = options.get("min_likelihood") or options.get("minLikelihood")
        include_quote = options.get("include_quote")
        if include_quote is None:
            include_quote = options.get("includeQuote")
        if include_quote is None:
            include_quote = True

        inspect_config: Dict[str, Any] = {"includeQuote": bool(include_quote)}
        if info_types:
            inspect_config["infoTypes"] = [{"name": t} for t in info_types]
        if min_likelihood:
            inspect_config["minLikelihood"] = min_likelihood
        min_per_type = options.get("min_likelihood_per_info_type") or options.get("minLikelihoodPerInfoType")
        if min_per_type:
            inspect_config["minLikelihoodPerInfoType"] = min_per_type
        limits = options.get("limits")
        if isinstance(limits, dict):
            inspect_config["limits"] = limits
        exclude_info_types = options.get("exclude_info_types")
        if exclude_info_types is None:
            exclude_info_types = options.get("excludeInfoTypes")
        if exclude_info_types is not None:
            inspect_config["excludeInfoTypes"] = bool(exclude_info_types)
        custom_info_types = options.get("custom_info_types") or options.get("customInfoTypes")
        if custom_info_types:
            inspect_config["customInfoTypes"] = custom_info_types
        rule_set = options.get("rule_set") or options.get("ruleSet")
        if rule_set:
            inspect_config["ruleSet"] = rule_set

        payload = {
            "item": {"value": text},
            "inspectConfig": inspect_config,
        }

        parent = f"projects/{self.project_id}"
        if self.location_id:
            parent = f"{parent}/locations/{self.location_id}"
        url = f"{self.base_url}/v2/{parent}/content:inspect"
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
            # Prefer codepoint offsets for text to align with Python string indexing.
            range_obj = location.get("codepointRange") or location.get("byteRange") or {}
            if not range_obj:
                content_locations = location.get("contentLocations") or []
                if content_locations and isinstance(content_locations[0], dict):
                    range_obj = (
                        content_locations[0].get("codepointRange")
                        or content_locations[0].get("byteRange")
                        or {}
                    )
            start = range_obj.get("start")
            end = range_obj.get("end")
            out.append(
                make_detected_entity(
                    entity_type=info_type,
                    score=finding.get("likelihood"),
                    begin_offset=start,
                    end_offset=end,
                    text=quote,
                    source="google_dlp",
                )
            )
        out.extend(detect_regex_entities(text, self.regex_rules))
        return out

    def normalize(self, entities: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
        return normalize_detected_entities(entities, source="google_dlp")
