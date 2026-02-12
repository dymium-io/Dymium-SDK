"""Backward-compatible re-exports for core contracts."""
from .llm import LLMClient
from .pii import PIIEngine
from .redaction import RedactionEngineProtocol
from .tools import ToolRegistry
from .runtime import AgentRuntime, RuntimeComponents
from dymium.types import (
    AgentEvent,
    ChatEvent,
    ChatRequest,
    PlaceholderMap,
    PlaceholderMapItem,
    ToolCall,
    ToolDefinition,
    ToolResult,
    ToolSettings,
)

__all__ = [
    "LLMClient",
    "PIIEngine",
    "RedactionEngineProtocol",
    "ToolRegistry",
    "AgentRuntime",
    "RuntimeComponents",
    "AgentEvent",
    "ChatEvent",
    "ChatRequest",
    "PlaceholderMap",
    "PlaceholderMapItem",
    "ToolCall",
    "ToolDefinition",
    "ToolResult",
    "ToolSettings",
]
