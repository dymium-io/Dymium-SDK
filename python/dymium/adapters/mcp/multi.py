"""Aggregate multiple MCP servers into a single tool registry."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from .external_mcp import ExternalMCPAdapter


class MultiMCPAdapter:
    def __init__(
        self,
        servers: List[Dict[str, Any]],
        prefix_tools: bool = True,
        separator: str = "::",
    ) -> None:
        if not servers:
            raise ValueError("MultiMCPAdapter requires at least one server config")
        self.prefix_tools = prefix_tools or len(servers) > 1
        self.separator = separator
        self._servers: List[Tuple[str, Any]] = []
        self._tool_map: Dict[str, Tuple[Any, str]] = {}
        self._server_map: Dict[str, Any] = {}

        for idx, cfg in enumerate(servers):
            name = cfg.get("name") or f"mcp{idx + 1}"
            adapter_name = cfg.get("adapter") or cfg.get("kind") or cfg.get("type") or "mcp"
            adapter = self._build_adapter(adapter_name, cfg)
            self._servers.append((name, adapter))
            self._server_map[name] = adapter

    def list_tools(self) -> Iterable[Dict[str, Any]]:
        self._tool_map.clear()
        out: List[Dict[str, Any]] = []
        for server_name, adapter in self._servers:
            tools = adapter.list_tools() or []
            for tool in tools:
                if not isinstance(tool, dict) or "name" not in tool:
                    continue
                original_name = tool["name"]
                name = self._prefix_name(server_name, original_name)
                self._tool_map[name] = (adapter, original_name)
                out.append({
                    "name": name,
                    "description": tool.get("description"),
                    "parameters": tool.get("parameters"),
                    "source": tool.get("source", "mcp"),
                    "tool_type": tool.get("tool_type"),
                    "direct_input_mode": tool.get("direct_input_mode"),
                })
        return out

    def execute(self, tool_call: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        if hasattr(tool_call, "model_dump"):
            tool_call = tool_call.model_dump()
        name = tool_call.get("name")
        if not name:
            raise ValueError("Tool call missing name")

        adapter, original_name = self._resolve_tool(name)
        call = dict(tool_call)
        call["name"] = original_name
        return adapter.execute(call, context)

    def _resolve_tool(self, name: str) -> Tuple[Any, str]:
        if not self._tool_map:
            self.list_tools()
        if name in self._tool_map:
            return self._tool_map[name]
        if self.separator in name:
            prefix, original = name.split(self.separator, 1)
            adapter = self._server_map.get(prefix)
            if adapter:
                return adapter, original
        if len(self._servers) == 1:
            return self._servers[0][1], name
        raise ValueError(f"Unknown tool: {name}")

    def _prefix_name(self, server_name: str, tool_name: str) -> str:
        if self.prefix_tools:
            return f"{server_name}{self.separator}{tool_name}"
        return tool_name

    @staticmethod
    def _build_adapter(adapter_name: str, cfg: Dict[str, Any]) -> Any:
        return ExternalMCPAdapter(
            base_url=cfg["base_url"],
            headers=cfg.get("headers"),
            timeout=cfg.get("timeout"),
        )
