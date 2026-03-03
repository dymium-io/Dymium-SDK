"""Tool policy helpers for framework integrations."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple

from dymium.tools import (
    TOOL_TYPE_DIRECT,
    normalize_input_mode,
    normalize_tool_type,
)


def extract_tool_policies(tools: Iterable[Any]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Read per-tool policy from the tool object itself.

    Required:
    - `tool_type` for every tool
    - `input_mode` for every `direct` tool
    """
    tool_types: Dict[str, str] = {}
    input_modes: Dict[str, str] = {}
    for tool in list(tools):
        name = _tool_name(tool)
        if not name:
            raise ValueError("Every tool must have a name for Dymium policy binding.")
        policy = _policy_from_metadata(tool, name=name)

        normalized_tool_type = normalize_tool_type(policy.get("tool_type"))
        tool_types[name] = normalized_tool_type

        raw_mode = policy.get("input_mode")
        if normalized_tool_type == TOOL_TYPE_DIRECT and raw_mode is None:
            raise ValueError(
                f"Tool {name!r} is direct but missing input_mode metadata. "
                "Set tool.metadata['dymium']['input_mode'] to 'protect' or 'resolve'."
            )
        if raw_mode is not None:
            input_modes[name] = normalize_input_mode(raw_mode)

    return tool_types, input_modes


def _tool_name(tool: Any) -> str | None:
    raw = _first_policy_value(_value(tool, "name"), _value(tool, "__name__"))
    if isinstance(raw, str):
        name = raw.strip()
        return name or None
    return None


def _policy_from_metadata(tool: Any, *, name: str) -> Dict[str, Any]:
    metadata = _value(tool, "metadata")
    if isinstance(metadata, dict):
        policy = metadata.get("dymium")
        if isinstance(policy, dict):
            return policy
    raise ValueError(
        f"Tool {name!r} must include tool.metadata['dymium'] "
        "with tool_type and input_mode policy."
    )


def _value(obj: Any, *keys: str) -> Any:
    if isinstance(obj, dict):
        return _dict_value(obj, *keys)
    for key in keys:
        try:
            value = getattr(obj, key)
        except Exception:
            value = None
        if value is not None:
            return value
    return None


def _dict_value(obj: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = obj.get(key)
        if value is not None:
            return value
    return None


def _first_policy_value(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None
