"""Tool type helpers for placeholder-boundary behavior."""
from __future__ import annotations

from typing import Any

TOOL_TYPE_NON_AGENTIC = "non_agentic"
TOOL_TYPE_AGENTIC = "agentic"
SUPPORTED_TOOL_TYPES = {TOOL_TYPE_NON_AGENTIC, TOOL_TYPE_AGENTIC}


def normalize_tool_type(value: Any) -> str:
    if value is None:
        return TOOL_TYPE_NON_AGENTIC
    tool_type = str(value).strip().lower()
    if tool_type in SUPPORTED_TOOL_TYPES:
        return tool_type
    return TOOL_TYPE_NON_AGENTIC
