"""External MCP adapter (third-party MCP servers)."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import json

from .jsonrpc import MCPJsonRpcClient


class ExternalMCPAdapter:
    def __init__(
        self,
        base_url: str,
        headers: Dict[str, str] | None = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.client = MCPJsonRpcClient(base_url=base_url, headers=headers or {}, timeout=timeout)

    def list_tools(self) -> Iterable[Dict[str, Any]]:
        result = self.client.list_tools()
        tools = result.get("tools", []) if isinstance(result, dict) else []
        out = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            out.append({
                "name": tool.get("name"),
                "description": tool.get("description"),
                "parameters": tool.get("inputSchema") or tool.get("parameters"),
                "source": "mcp",
                "tool_type": tool.get("tool_type") or tool.get("toolType"),
                "direct_input_mode": tool.get("direct_input_mode") or tool.get("directInputMode"),
            })
        return out

    def execute(self, tool_call: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        if hasattr(tool_call, "model_dump"):
            tool_call = tool_call.model_dump()
        name = tool_call.get("name")
        arguments = tool_call.get("arguments") or {}
        result = self.client.call_tool(name, arguments)
        parsed = self._parse_tool_result(result)
        return {
            "id": tool_call.get("id"),
            "name": name,
            "result": parsed,
            "isError": result.get("isError") if isinstance(result, dict) else None,
        }

    @staticmethod
    def _parse_tool_result(result: Any) -> Any:
        if not isinstance(result, dict):
            return result
        content = result.get("content")
        if isinstance(content, list):
            texts = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "json" and "json" in item:
                    return item["json"]
                if item.get("type") == "text" and "text" in item:
                    texts.append(item["text"])
            if len(texts) == 1:
                return ExternalMCPAdapter._maybe_json(texts[0])
            if texts:
                return "\n".join(texts)
        return result

    @staticmethod
    def _maybe_json(text: str) -> Any:
        try:
            return json.loads(text)
        except Exception:
            return text
