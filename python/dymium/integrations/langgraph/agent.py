"""High-level LangGraph agent builder with Dymium sanitization."""
from __future__ import annotations

from typing import Any, Dict, Sequence

from dymium.runtime.secure_runtime import DEFAULT_SYSTEM_PROMPT
from dymium.sanitization import Sanitizer

from .state import DymiumMessagesState, sanitize_state_messages
from .tools import make_tool_node


def create_sanitized_agent(
    model: Any,
    tools: Sequence[Any],
    sanitizer: Sanitizer,
    *,
    system_prompt: str | None = DEFAULT_SYSTEM_PROMPT,
    messages_key: str = "messages",
    state_schema: Any = DymiumMessagesState,
    max_tool_calls: int | None = None,
):
    """Create a compiled LangGraph app with sanitized model and tool boundaries."""
    try:
        from langgraph.graph import END, StateGraph
        from langgraph.prebuilt import tools_condition
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("LangGraph is not installed. Install with: pip install langgraph") from exc

    if hasattr(model, "bind_tools"):
        model = model.bind_tools(list(tools))

    tools_node = make_tool_node(
        tools,
        sanitizer,
        messages_key=messages_key,
    )

    def model_node(state: Dict[str, Any]) -> Dict[str, Any]:
        updates = sanitize_state_messages(
            state,
            sanitizer,
            system_prompt=system_prompt,
            messages_key=messages_key,
        )
        ai_msg = model.invoke(updates[messages_key])
        return {
            messages_key: [ai_msg],
            "placeholder_map": updates["placeholder_map"],
            "security_summary": updates["security_summary"],
            "last_sanitized_index": updates["last_sanitized_index"],
        }

    def route(state: Dict[str, Any]) -> str:
        next_node = tools_condition(state, messages_key=messages_key)
        if next_node == "tools" and max_tool_calls is not None:
            tool_usage = (state.get("security_summary") or {}).get("tool_usage", {})
            if int(tool_usage.get("tool_calls_count", 0)) >= int(max_tool_calls):
                return "__end__"
        return next_node

    graph = StateGraph(state_schema)
    graph.add_node("model", model_node)
    graph.add_node("tools", tools_node)
    graph.add_conditional_edges("model", route, {"tools": "tools", "__end__": END, END: END})
    graph.add_edge("tools", "model")
    graph.set_entry_point("model")
    return graph.compile()
