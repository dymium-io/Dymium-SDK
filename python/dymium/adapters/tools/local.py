"""Local tool adapter (callable-based tools)."""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List

from dymium.delegation import DelegatedTransport
from dymium.delegation.transport import pop_runtime_context, push_runtime_context
from dymium.tools import (
    TOOL_TYPE_DIRECT,
    TOOL_TYPE_DELEGATED,
    normalize_input_mode,
    normalize_tool_type,
)


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
                "source": spec.get("source") or "local",
                "tool_type": spec.get("tool_type"),
                "input_mode": spec.get("input_mode"),
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
        delegated_transport: Any = None
        source: str | None = None
        if isinstance(tool, dict):
            handler = tool.get("handler") or tool.get("callable") or tool.get("fn")
            name = tool.get("name")
            description = tool.get("description")
            parameters = tool.get("parameters") or tool.get("input_schema")
            tool_type = tool.get("tool_type")
            input_mode = tool.get("input_mode")
            delegated_transport = tool.get("delegated_transport") or tool.get("delegatedTransport")
            source = tool.get("source")
        else:
            handler = tool.invoke if hasattr(tool, "invoke") else tool
            name = getattr(tool, "name", None) or getattr(handler, "__name__", None)
            description = getattr(tool, "description", None) or getattr(handler, "__doc__", None)
            parameters = getattr(tool, "parameters", None)
            tool_type = getattr(tool, "tool_type", None) or getattr(handler, "tool_type", None)
            input_mode = (
                getattr(tool, "input_mode", None)
                or getattr(handler, "input_mode", None)
            )
            delegated_transport = (
                getattr(tool, "delegated_transport", None)
                or getattr(handler, "delegated_transport", None)
            )
            source = getattr(tool, "source", None) or getattr(handler, "source", None)

        if not name:
            raise ValueError("Local tool is missing a name")
        if not callable(handler):
            if delegated_transport is not None:
                transport = DelegatedTransport(delegated_transport, name=name)
                handler = transport.as_tool_handler()
                source = source or "delegated_transport"
            else:
                raise ValueError("Local tool must provide a callable handler")

        if tool_type is None:
            raise ValueError(
                f"Local tool {name!r} must declare tool_type ('direct' or 'delegated')."
            )
        normalized_tool_type = normalize_tool_type(tool_type)
        if normalized_tool_type == TOOL_TYPE_DIRECT and input_mode is None:
            raise ValueError(
                f"Local tool {name!r} is direct and must declare input_mode ('protect' or 'resolve')."
            )

        return {
            "name": name,
            "description": (description or "").strip() or None,
            "parameters": parameters,
            "handler": handler,
            "source": source or "local",
            "tool_type": normalized_tool_type,
            "input_mode": normalize_input_mode(input_mode) if input_mode is not None else None,
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
        runtime_token = None
        if normalize_tool_type(tool_type) == TOOL_TYPE_DELEGATED:
            # Runtime-owned context only: never forward dymium_context as tool input.
            call_args.pop("dymium_context", None)
            dymium_context = context.get("dymium_context")
            if isinstance(dymium_context, dict):
                runtime_token = push_runtime_context(dymium_context)
                if bool(getattr(handler, "__dymium_context_from_transport__", False)):
                    call_args["dymium_context"] = dymium_context
        try:
            return handler(**call_args)
        finally:
            if runtime_token is not None:
                pop_runtime_context(runtime_token)
