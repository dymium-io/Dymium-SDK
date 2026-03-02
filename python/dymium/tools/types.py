"""Tool type helpers for placeholder-boundary behavior."""
from __future__ import annotations

from typing import Any

TOOL_TYPE_DIRECT = "direct"
TOOL_TYPE_DELEGATED = "delegated"
SUPPORTED_TOOL_TYPES = {TOOL_TYPE_DIRECT, TOOL_TYPE_DELEGATED}

DIRECT_INPUT_MODE_RESOLVE = "resolve"
DIRECT_INPUT_MODE_PROTECT = "protect"
SUPPORTED_DIRECT_INPUT_MODES = {
    DIRECT_INPUT_MODE_RESOLVE,
    DIRECT_INPUT_MODE_PROTECT,
}


def normalize_tool_type(value: Any) -> str:
    if value is None:
        return TOOL_TYPE_DIRECT
    tool_type = str(value).strip().lower()
    if tool_type in SUPPORTED_TOOL_TYPES:
        return tool_type
    raise ValueError(f"Unsupported tool_type: {value!r}. Expected one of: {sorted(SUPPORTED_TOOL_TYPES)}")


def normalize_direct_input_mode(value: Any) -> str:
    if value is None:
        return DIRECT_INPUT_MODE_RESOLVE
    mode = str(value).strip().lower()
    if mode in SUPPORTED_DIRECT_INPUT_MODES:
        return mode
    raise ValueError(
        f"Unsupported direct_input_mode: {value!r}. "
        f"Expected one of: {sorted(SUPPORTED_DIRECT_INPUT_MODES)}"
    )


def should_resolve_tool_inputs(
    *,
    tool_type: Any,
    direct_input_mode: Any = None,
) -> bool:
    if normalize_tool_type(tool_type) == TOOL_TYPE_DELEGATED:
        return False
    return normalize_direct_input_mode(direct_input_mode) == DIRECT_INPUT_MODE_RESOLVE
