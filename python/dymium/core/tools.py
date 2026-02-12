"""Tool registry contract."""
from __future__ import annotations

from typing import Any, Iterable, Protocol

from dymium.types import ToolCall, ToolDefinition, ToolResult

class ToolRegistry(Protocol):
    def list_tools(self) -> Iterable[ToolDefinition]:
        """Return tool definitions."""
        raise NotImplementedError

    def execute(self, tool_call: ToolCall, context: dict[str, Any]) -> ToolResult:
        """Execute a tool call inside a placeholder-aware boundary."""
        raise NotImplementedError
