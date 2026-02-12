"""GhostLLM adapter (Dymium-hosted LLM gateway)."""
from __future__ import annotations

from typing import Any, Dict, Iterable


class GhostLLMAdapter:
    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def chat(self, request: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def stream(self, request: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        raise NotImplementedError

    def supports_tool_calling(self) -> bool:
        return True
