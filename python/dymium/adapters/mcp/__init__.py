"""MCP adapters (GhostMCP + external servers)."""

from .external_mcp import ExternalMCPAdapter
from .multi import MultiMCPAdapter

__all__ = [
    "ExternalMCPAdapter",
    "MultiMCPAdapter",
]
