"""Agent runtime contract and dependency wiring."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Protocol

from dymium.types import ChatRequest

from .llm import LLMClient
from .pii import PIIEngine
from .redaction import RedactionEngineProtocol
from .tools import ToolRegistry


class AgentRuntime(Protocol):
    def run(self, request: ChatRequest | Dict[str, Any]) -> Dict[str, Any]:
        """Run a full agent loop and return a final response."""
        raise NotImplementedError

    def invoke(self, request: ChatRequest | Dict[str, Any]) -> Dict[str, Any]:
        """Alias for run, matching common framework entrypoints."""
        raise NotImplementedError

    def stream(self, request: ChatRequest | Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        """Run a full agent loop and stream events."""
        raise NotImplementedError


@dataclass
class RuntimeComponents:
    """Container for wiring dependencies into the SecureRuntime."""
    llm: LLMClient
    pii: PIIEngine
    redaction: RedactionEngineProtocol
    tools: ToolRegistry
