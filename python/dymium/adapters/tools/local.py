"""Local tool adapter (callable-based tools)."""
from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, Iterable, List

from dymium.tools import TOOL_TYPE_AGENTIC, normalize_tool_type


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
                "tool_type": spec.get("tool_type"),
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
        result = self._invoke_handler(
            handler,
            args,
            context,
            tool_type=spec.get("tool_type"),
        )
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
            tool_type = tool.get("tool_type") or tool.get("toolType")
        else:
            handler = tool.invoke if hasattr(tool, "invoke") else tool
            name = getattr(tool, "name", None) or getattr(handler, "__name__", None)
            description = getattr(tool, "description", None) or getattr(handler, "__doc__", None)
            parameters = getattr(tool, "parameters", None)
            tool_type = getattr(tool, "tool_type", None) or getattr(handler, "tool_type", None)

        if not callable(handler):
            raise ValueError("Local tool must provide a callable handler")
        if not name:
            raise ValueError("Local tool is missing a name")

        return {
            "name": name,
            "description": (description or "").strip() or None,
            "parameters": parameters,
            "handler": handler,
            "tool_type": normalize_tool_type(tool_type),
        }

    @staticmethod
    def _invoke_handler(
        handler: Callable[..., Any],
        args: Any,
        context: Dict[str, Any],
        *,
        tool_type: str | None = None,
    ) -> Any:
        if not isinstance(args, dict):
            return handler(args)
        call_args = dict(args)
        if (
            normalize_tool_type(tool_type) == TOOL_TYPE_AGENTIC
            and "dymium_context" not in call_args
            and LocalToolAdapter._accepts_named_arg(handler, "dymium_context")
        ):
            dymium_context = context.get("dymium_context")
            if isinstance(dymium_context, dict):
                call_args["dymium_context"] = dymium_context
        return handler(**call_args)

    @staticmethod
    def _accepts_named_arg(handler: Callable[..., Any], name: str) -> bool:
        try:
            params = inspect.signature(handler).parameters.values()
        except Exception:
            return False
        for param in params:
            if param.kind == inspect.Parameter.VAR_KEYWORD:
                return True
            if param.name == name and param.kind in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                return True
        return False
