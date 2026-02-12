"""Local tool adapter (callable-based tools)."""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List


class LocalToolAdapter:
    def __init__(self, tools: Iterable[Any]) -> None:
        self._tools: List[Dict[str, Any]] = []
        self._tool_map: Dict[str, Dict[str, Any]] = {}
        for tool in tools:
            spec = self._normalize_tool(tool)
            name = spec["name"]
            if name in self._tool_map:
                raise ValueError(f"Duplicate local tool name: {name}")
            self._tools.append(spec)
            self._tool_map[name] = spec

    def list_tools(self) -> Iterable[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for spec in self._tools:
            out.append({
                "name": spec["name"],
                "description": spec.get("description"),
                "parameters": spec.get("parameters") or {"type": "object", "properties": {}},
                "source": "local",
            })
        return out

    def execute(self, tool_call: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        if hasattr(tool_call, "model_dump"):
            tool_call = tool_call.model_dump()
        name = tool_call.get("name")
        if not name:
            raise ValueError("Tool call missing name")
        spec = self._tool_map.get(name)
        if not spec:
            raise ValueError(f"Unknown local tool: {name}")
        handler = spec["handler"]
        args = tool_call.get("arguments") or {}
        if isinstance(args, dict):
            result = handler(**args)
        else:
            result = handler(args)
        return {
            "id": tool_call.get("id"),
            "name": name,
            "result": result,
            "isError": None,
        }

    @staticmethod
    def _normalize_tool(tool: Any) -> Dict[str, Any]:
        if isinstance(tool, dict):
            handler = tool.get("handler") or tool.get("callable") or tool.get("fn")
            name = tool.get("name")
            description = tool.get("description")
            parameters = tool.get("parameters") or tool.get("input_schema")
        else:
            handler = tool.invoke if hasattr(tool, "invoke") else tool
            name = getattr(tool, "name", None) or getattr(handler, "__name__", None)
            description = getattr(tool, "description", None) or getattr(handler, "__doc__", None)
            parameters = getattr(tool, "parameters", None)

        if not callable(handler):
            raise ValueError("Local tool must provide a callable handler")
        if not name:
            raise ValueError("Local tool is missing a name")

        return {
            "name": name,
            "description": (description or "").strip() or None,
            "parameters": parameters,
            "handler": handler,
        }
