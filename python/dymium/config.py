"""Runtime configuration objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Union, List, Tuple


@dataclass
class RuntimeConfig:
    llm: Optional[str] = None
    pii: Optional[str] = None
    tools: Optional[Union[str, List[Any]]] = None
    model: Optional[str] = None

    llm_config: Dict[str, Any] = field(default_factory=dict)
    model_config: Dict[str, Any] = field(default_factory=dict)
    pii_config: Dict[str, Any] = field(default_factory=dict)
    tools_config: Dict[str, Any] = field(default_factory=dict)

    redaction_config: Dict[str, Any] = field(default_factory=dict)
    runtime_config: Dict[str, Any] = field(default_factory=dict)
    # Optional per-tool behavior override: {"tool_name": "non_agentic|agentic"}.
    tool_types: Dict[str, str] = field(default_factory=dict)
    # Nicer public config for MCP (single server dict or list of servers).
    mcp: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None

    def validate(self) -> None:
        if not self.model and not self.llm:
            raise ValueError("RuntimeConfig.model or RuntimeConfig.llm is required")
        if not self.pii:
            raise ValueError("RuntimeConfig.pii is required")
        if self.mcp is None and not self.tools:
            raise ValueError("RuntimeConfig.tools is required unless mcp is provided")
        if self.mcp is not None and not isinstance(self.mcp, (dict, list)):
            raise ValueError("RuntimeConfig.mcp must be a dict or list of dicts")
        if not isinstance(self.tool_types, dict):
            raise ValueError("RuntimeConfig.tool_types must be a dict")

    def resolve_llm(self) -> Tuple[str, Dict[str, Any]]:
        """Resolve provider + config, preferring model/model_config."""
        if self.model:
            provider, model_name = _split_model(self.model)
            if not provider:
                if self.llm:
                    provider = self.llm
                else:
                    raise ValueError("RuntimeConfig.model must include provider prefix (e.g., openai:gpt-5)")
            if self.llm and provider != self.llm:
                raise ValueError("RuntimeConfig.model provider does not match RuntimeConfig.llm")

            cfg: Dict[str, Any] = dict(self.llm_config or {})
            cfg.update(self.model_config or {})
            if model_name and "model" not in cfg:
                cfg["model"] = model_name
            return provider, cfg

        if not self.llm:
            raise ValueError("RuntimeConfig.llm is required when model is not set")
        return self.llm, dict(self.llm_config or {})


def _split_model(model: str) -> Tuple[str | None, str | None]:
    if ":" not in model:
        return None, model or None
    provider, name = model.split(":", 1)
    return provider or None, name or None
