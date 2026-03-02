"""Adapter registry and factories.

Adapters register under simple string keys (e.g., "openai", "presidio").
This keeps the public API clean and allows plugin-style extension later.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Type


@dataclass
class AdapterEntry:
    name: str
    factory: Callable[[Dict[str, Any]], Any]
    kind: str  # "llm" | "pii" | "tools"


class AdapterRegistry:
    def __init__(self) -> None:
        self._entries: Dict[str, AdapterEntry] = {}

    def register(self, name: str, kind: str, factory: Callable[[Dict[str, Any]], Any]) -> None:
        key = f"{kind}:{name}".lower()
        self._entries[key] = AdapterEntry(name=name, kind=kind, factory=factory)

    def create(self, kind: str, name: str, config: Optional[Dict[str, Any]] = None) -> Any:
        key = f"{kind}:{name}".lower()
        if key not in self._entries:
            raise ValueError(f"Unknown adapter: {kind}:{name}")
        entry = self._entries[key]
        return entry.factory(config or {})

    def list(self, kind: Optional[str] = None) -> Dict[str, AdapterEntry]:
        if kind is None:
            return dict(self._entries)
        prefix = f"{kind}:"
        return {k: v for k, v in self._entries.items() if k.startswith(prefix)}


GLOBAL_REGISTRY = AdapterRegistry()


def register_default_adapters() -> None:
    """Register built-in adapters.

    This import is intentionally local to avoid heavy imports on module load.
    """
    from dymium.adapters.llm import OpenAIAdapter, GhostLLMAdapter, AnthropicAdapter, GeminiAdapter
    from dymium.adapters.pii import (
        GhostPIIDetector,
        PresidioDetector,
        ComprehendDetector,
        GoogleDLPDetector,
        AzurePIIDetector,
        HuggingFacePIIDetector,
    )
    from dymium.adapters.mcp import ExternalMCPAdapter, MultiMCPAdapter

    GLOBAL_REGISTRY.register(
        name="openai",
        kind="llm",
        factory=lambda cfg: OpenAIAdapter(
            api_key=cfg["api_key"],
            default_model=cfg.get("model", "gpt-5"),
            max_tokens=cfg.get("max_tokens"),
            top_p=cfg.get("top_p"),
            presence_penalty=cfg.get("presence_penalty"),
            frequency_penalty=cfg.get("frequency_penalty"),
            stop=cfg.get("stop"),
        ),
    )

    GLOBAL_REGISTRY.register(
        name="ghostllm",
        kind="llm",
        factory=lambda cfg: GhostLLMAdapter(
            base_url=cfg["base_url"],
            api_key=cfg.get("api_key"),
        ),
    )

    GLOBAL_REGISTRY.register(
        name="anthropic",
        kind="llm",
        factory=lambda cfg: AnthropicAdapter(
            api_key=cfg["api_key"],
            base_url=cfg.get("base_url", "https://api.anthropic.com"),
            version=cfg.get("version", "2023-06-01"),
            default_model=cfg.get("model", "claude-3-5-sonnet-20240620"),
            max_tokens=cfg.get("max_tokens", 1024),
        ),
    )

    GLOBAL_REGISTRY.register(
        name="gemini",
        kind="llm",
        factory=lambda cfg: GeminiAdapter(
            api_key=cfg["api_key"],
            base_url=cfg.get("base_url", "https://generativelanguage.googleapis.com/v1beta"),
            default_model=cfg.get("model", "gemini-1.5-pro"),
        ),
    )

    GLOBAL_REGISTRY.register(
        name="ghostpii",
        kind="pii",
        factory=lambda cfg: GhostPIIDetector(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            timeout_s=cfg.get("timeout_s", 10),
            entity_types=cfg.get("entity_types") or cfg.get("entityTypes"),
            endpoint_path=cfg.get("endpoint_path", "/v1/detect/pii"),
            language=cfg.get("language", "en"),
            user_patterns=cfg.get("user_patterns"),
            regex_rules=cfg.get("regex_rules") or cfg.get("regexRules"),
        ),
    )

    GLOBAL_REGISTRY.register(
        name="presidio",
        kind="pii",
        factory=lambda cfg: PresidioDetector(
            base_url=cfg["base_url"],
            timeout_s=cfg.get("timeout_s", 10),
            language=cfg.get("language", "en"),
            entities=cfg.get("entities"),
            score_threshold=cfg.get("score_threshold"),
            regex_rules=cfg.get("regex_rules") or cfg.get("regexRules"),
        ),
    )

    GLOBAL_REGISTRY.register(
        name="comprehend",
        kind="pii",
        factory=lambda cfg: ComprehendDetector(
            region=cfg["region"],
            credentials=cfg.get("credentials"),
            endpoint_url=cfg.get("endpoint_url"),
            language_code=cfg.get("language_code", "en"),
            regex_rules=cfg.get("regex_rules") or cfg.get("regexRules"),
        ),
    )

    GLOBAL_REGISTRY.register(
        name="google_dlp",
        kind="pii",
        factory=lambda cfg: GoogleDLPDetector(
            project_id=cfg["project_id"],
            credentials=cfg.get("credentials"),
            location_id=cfg.get("location_id"),
            base_url=cfg.get("base_url", "https://dlp.googleapis.com"),
            timeout_s=cfg.get("timeout_s", 10),
            info_types=cfg.get("info_types") or cfg.get("infoTypes"),
            min_likelihood=cfg.get("min_likelihood") or cfg.get("minLikelihood"),
            include_quote=cfg.get("include_quote", cfg.get("includeQuote", True)),
            min_likelihood_per_info_type=cfg.get("min_likelihood_per_info_type") or cfg.get("minLikelihoodPerInfoType"),
            limits=cfg.get("limits"),
            exclude_info_types=cfg.get("exclude_info_types", cfg.get("excludeInfoTypes")),
            custom_info_types=cfg.get("custom_info_types") or cfg.get("customInfoTypes"),
            rule_set=cfg.get("rule_set") or cfg.get("ruleSet"),
            regex_rules=cfg.get("regex_rules") or cfg.get("regexRules"),
        ),
    )

    GLOBAL_REGISTRY.register(
        name="azure_pii",
        kind="pii",
        factory=lambda cfg: AzurePIIDetector(
            endpoint=cfg["endpoint"],
            api_key=cfg.get("api_key"),
            bearer_token=cfg.get("bearer_token") or cfg.get("token"),
            timeout_s=cfg.get("timeout_s", 10),
            api_version=cfg.get("api_version", "2022-05-01"),
            use_legacy_endpoint=cfg.get("use_legacy_endpoint", False),
            language=cfg.get("language", "en"),
            parameters=cfg.get("parameters"),
            regex_rules=cfg.get("regex_rules") or cfg.get("regexRules"),
        ),
    )

    GLOBAL_REGISTRY.register(
        name="huggingface",
        kind="pii",
        factory=lambda cfg: HuggingFacePIIDetector(
            model_id=cfg.get("model_id", "dymium/Dymium-NER-v1"),
            aggregation_strategy=cfg.get("aggregation_strategy", "simple"),
            score_threshold=cfg.get("score_threshold"),
            device=cfg.get("device"),
            tokenizer=cfg.get("tokenizer"),
            pipeline_kwargs=cfg.get("pipeline_kwargs") or cfg.get("pipelineKwargs"),
            regex_rules=cfg.get("regex_rules") or cfg.get("regexRules"),
        ),
    )

    GLOBAL_REGISTRY.register(
        name="dymium_hf",
        kind="pii",
        factory=lambda cfg: HuggingFacePIIDetector(
            model_id=cfg.get("model_id", "dymium/Dymium-NER-v1"),
            aggregation_strategy=cfg.get("aggregation_strategy", "simple"),
            score_threshold=cfg.get("score_threshold"),
            device=cfg.get("device"),
            tokenizer=cfg.get("tokenizer"),
            pipeline_kwargs=cfg.get("pipeline_kwargs") or cfg.get("pipelineKwargs"),
            regex_rules=cfg.get("regex_rules") or cfg.get("regexRules"),
        ),
    )

    GLOBAL_REGISTRY.register(
        name="ghostmcp",
        kind="tools",
        factory=lambda cfg: ExternalMCPAdapter(
            base_url=cfg["base_url"],
            headers=_merge_auth_headers(cfg.get("headers"), cfg.get("api_key")),
        ),
    )

    GLOBAL_REGISTRY.register(
        name="mcp",
        kind="tools",
        factory=lambda cfg: ExternalMCPAdapter(
            base_url=cfg["base_url"],
            headers=cfg.get("headers"),
        ),
    )

    GLOBAL_REGISTRY.register(
        name="mcp_multi",
        kind="tools",
        factory=lambda cfg: MultiMCPAdapter(
            servers=_normalize_mcp_servers(cfg.get("servers", [])),
            prefix_tools=cfg.get("prefix_tools", True),
            separator=cfg.get("separator", "::"),
        ),
    )


def _merge_auth_headers(headers: Optional[Dict[str, str]], api_key: Optional[str]) -> Dict[str, str]:
    merged = dict(headers or {})
    if api_key:
        merged.setdefault("Authorization", f"Bearer {api_key}")
    return merged


def _normalize_mcp_servers(servers: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    out = []
    for cfg in servers:
        merged = dict(cfg)
        if "headers" not in merged or merged["headers"] is None:
            merged["headers"] = {}
        if merged.get("api_key"):
            merged["headers"] = _merge_auth_headers(merged.get("headers"), merged.get("api_key"))
        out.append(merged)
    return out
