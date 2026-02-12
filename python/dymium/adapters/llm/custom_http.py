"""Custom LLM adapter for arbitrary HTTP endpoints.

This adapter is intended for user-provided endpoints that conform to a simple
chat/completions contract. Mapping logic will be defined by user config.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable


class CustomHTTPAdapter:
    def __init__(self, base_url: str, headers: Dict[str, str] | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}

    def chat(self, request: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def stream(self, request: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        raise NotImplementedError

    def supports_tool_calling(self) -> bool:
        return False
