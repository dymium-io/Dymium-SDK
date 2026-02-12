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
    from dymium.adapters.pii import GhostPIIDetector, PresidioDetector, ComprehendDetector, GoogleDLPDetector, AzurePIIDetector
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
            api_key=cfg.get("api_key"),
            timeout_s=cfg.get("timeout_s", 10),
            regex_rules=cfg.get("regex_rules") or cfg.get("regexRules"),
        ),
    )

    GLOBAL_REGISTRY.register(
        name="presidio",
        kind="pii",
        factory=lambda cfg: PresidioDetector(
            base_url=cfg["base_url"],
            timeout_s=cfg.get("timeout_s", 10),
            regex_rules=cfg.get("regex_rules") or cfg.get("regexRules"),
        ),
    )

    GLOBAL_REGISTRY.register(
        name="comprehend",
        kind="pii",
        factory=lambda cfg: ComprehendDetector(
            region=cfg["region"],
            credentials=cfg.get("credentials"),
            regex_rules=cfg.get("regex_rules") or cfg.get("regexRules"),
        ),
    )

    GLOBAL_REGISTRY.register(
        name="google_dlp",
        kind="pii",
        factory=lambda cfg: GoogleDLPDetector(
            project_id=cfg["project_id"],
            credentials=cfg.get("credentials"),
            base_url=cfg.get("base_url", "https://dlp.googleapis.com"),
            timeout_s=cfg.get("timeout_s", 10),
            regex_rules=cfg.get("regex_rules") or cfg.get("regexRules"),
        ),
    )

    GLOBAL_REGISTRY.register(
        name="azure_pii",
        kind="pii",
        factory=lambda cfg: AzurePIIDetector(
            endpoint=cfg["endpoint"],
            api_key=cfg["api_key"],
            timeout_s=cfg.get("timeout_s", 10),
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
