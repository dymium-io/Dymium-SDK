"""LLM client contract."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Protocol


class LLMClient(Protocol):
    def chat(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Send a non-streaming chat/completions request."""
        raise NotImplementedError

    def stream(self, request: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        """Stream chat/completions responses as an event iterator."""
        raise NotImplementedError

    def supports_tool_calling(self) -> bool:
        """Return True if the provider supports native tool calling."""
        raise NotImplementedError
