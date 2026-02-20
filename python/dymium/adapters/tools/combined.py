"""Combine multiple tool registries into a single registry."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple


class CombinedToolAdapter:
    def __init__(
        self,
        registries: Iterable[Tuple[str, Any]],
        separator: str = "::",
        prefix_on_conflict: bool = True,
    ) -> None:
        self.separator = separator
        self.prefix_on_conflict = prefix_on_conflict
        self._registries: List[Tuple[str, Any]] = list(registries)
        if not self._registries:
            raise ValueError("CombinedToolAdapter requires at least one registry")
        self._tool_map: Dict[str, Tuple[Any, str]] = {}

    def list_tools(self) -> Iterable[Dict[str, Any]]:
        self._tool_map.clear()
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        seen_map: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        conflicts: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}

        for registry_name, registry in self._registries:
            tools = registry.list_tools() or []
            for tool in tools:
                if not isinstance(tool, dict) or "name" not in tool:
                    continue
                tool_name = tool["name"]
                if tool_name in seen:
                    if tool_name not in conflicts:
                        conflicts[tool_name] = [seen_map[tool_name]]
                    conflicts[tool_name].append((registry_name, tool))
                else:
                    seen.add(tool_name)
                    seen_map[tool_name] = (registry_name, tool)
                    self._tool_map[tool_name] = (registry, tool_name)
                    out.append({
                        "name": tool_name,
                        "description": tool.get("description"),
                        "parameters": tool.get("parameters"),
                        "source": tool.get("source"),
                        "tool_type": tool.get("tool_type"),
                    })

        if conflicts and self.prefix_on_conflict:
            for name, entries in conflicts.items():
                # Remove the unprefixed entry and replace with prefixed versions.
                out = [tool for tool in out if tool.get("name") != name]
                self._tool_map.pop(name, None)
                for registry_name, tool in entries:
                    prefixed = f"{registry_name}{self.separator}{name}"
                    self._tool_map[prefixed] = (self._registry_by_name(registry_name), name)
                    out.append({
                        "name": prefixed,
                        "description": tool.get("description"),
                        "parameters": tool.get("parameters"),
                        "source": tool.get("source"),
                        "tool_type": tool.get("tool_type"),
                    })

        if conflicts and not self.prefix_on_conflict:
            conflict_names = ", ".join(sorted(conflicts.keys()))
            raise ValueError(f"Tool name conflicts: {conflict_names}")

        return out

    def execute(self, tool_call: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        if hasattr(tool_call, "model_dump"):
            tool_call = tool_call.model_dump()
        name = tool_call.get("name")
        if not name:
            raise ValueError("Tool call missing name")
        registry, original_name = self._resolve_tool(name)
        call = dict(tool_call)
        call["name"] = original_name
        return registry.execute(call, context)

    def _resolve_tool(self, name: str) -> Tuple[Any, str]:
        if not self._tool_map:
            self.list_tools()
        if name in self._tool_map:
            return self._tool_map[name]
        if self.separator in name:
            prefix, original = name.split(self.separator, 1)
            registry = self._registry_by_name(prefix)
            if registry is not None:
                return registry, original
        raise ValueError(f"Unknown tool: {name}")

    def _registry_by_name(self, name: str) -> Any | None:
        for registry_name, registry in self._registries:
            if registry_name == name:
                return registry
        return None
