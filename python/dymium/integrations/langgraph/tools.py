"""LangGraph tool integration."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Dict, Sequence

from dymium.sanitization import Sanitizer, SanitizationContext, ensure_security_summary


def make_tool_call_wrapper(
    sanitizer: Sanitizer,
    *,
    messages_key: str = "messages",
) -> Callable[[Any, Callable[[Any], Any]], Any]:
    def wrap_tool_call(request: Any, handler: Callable[[Any], Any]) -> Any:
        state = request.state if hasattr(request, "state") else {}
        base_map = dict(getattr(state, "get", lambda k, d=None: d)("placeholder_map", {}) or {})
        ctx = SanitizationContext(
            placeholder_map=dict(base_map),
            security_summary=ensure_security_summary(),
        )

        tool_call = request.tool_call
        tool_name = tool_call.get("name") if isinstance(tool_call, dict) else None
        tool_args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}

        sanitizer.record_tool_call(tool_name, ctx)

        resolved_args = sanitizer.resolve_for_tool(tool_args, ctx)
        tool_call = dict(tool_call)
        tool_call["args"] = resolved_args

        if hasattr(request, "override"):
            request = request.override(tool_call=tool_call)

        result = handler(request)

        sanitized_result = sanitizer.sanitize_tool_output(
            _tool_result_payload(result),
            ctx,
            tool_name,
        )

        map_delta = _map_delta(base_map, ctx.placeholder_map)
        return _merge_tool_result(result, sanitized_result, map_delta, ctx, state, messages_key)

    return wrap_tool_call


def make_tool_node(
    tools: Sequence[Any],
    sanitizer: Sanitizer,
    *,
    messages_key: str = "messages",
    **kwargs: Any,
):
    try:
        from langgraph.prebuilt import ToolNode
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("LangGraph is not installed. Install with: pip install langgraph") from exc

    wrap = make_tool_call_wrapper(sanitizer, messages_key=messages_key)
    return ToolNode(tools, messages_key=messages_key, wrap_tool_call=wrap, **kwargs)


def _tool_result_payload(result: Any) -> Any:
    if hasattr(result, "content"):
        return getattr(result, "content")
    if isinstance(result, dict) and "content" in result:
        return result["content"]
    return result


def _merge_tool_result(
    result: Any,
    sanitized: Any,
    map_delta: Dict[str, str],
    ctx: SanitizationContext,
    state: Any,
    messages_key: str,
) -> Any:
    try:
        from langgraph.types import Command
    except Exception:
        Command = None  # type: ignore

    # Determine existing message count to advance last_sanitized_index
    existing_count = 0
    if isinstance(state, dict):
        existing_count = len(state.get(messages_key, []) or [])

    if Command is not None and isinstance(result, Command):
        update = result.update
        if isinstance(update, dict):
            merged = dict(update)
            if messages_key in merged and isinstance(merged[messages_key], list):
                merged[messages_key] = _set_messages_content(merged[messages_key], sanitized)
            merged["placeholder_map"] = map_delta
            merged["security_summary"] = ctx.security_summary
            if messages_key in merged and isinstance(merged[messages_key], list):
                merged["last_sanitized_index"] = existing_count + len(merged[messages_key])
            return replace(result, update=merged)
        if isinstance(update, list):
            safe_update = _set_messages_content(update, sanitized)
            return Command(
                graph=result.graph,
                update={
                    messages_key: safe_update,
                    "placeholder_map": map_delta,
                    "security_summary": ctx.security_summary,
                    "last_sanitized_index": existing_count + len(safe_update),
                },
                resume=result.resume,
                goto=result.goto,
            )
        return result

    if Command is not None:
        safe_result = _set_result_content(result, sanitized)
        return Command(
            update={
                messages_key: [safe_result],
                "placeholder_map": map_delta,
                "security_summary": ctx.security_summary,
                "last_sanitized_index": existing_count + 1,
            }
        )

    # Fallback: mutate state
    if isinstance(state, dict):
        merged_map = dict(state.get("placeholder_map") or {})
        merged_map.update(map_delta)
        state["placeholder_map"] = merged_map
        state["security_summary"] = ctx.security_summary
        state["last_sanitized_index"] = existing_count + 1
    return _set_result_content(result, sanitized)


def _map_delta(base: Dict[str, str], updated: Dict[str, str]) -> Dict[str, str]:
    delta: Dict[str, str] = {}
    for k, v in updated.items():
        if base.get(k) != v:
            delta[k] = v
    return delta


def _set_messages_content(messages: list[Any], content: Any) -> list[Any]:
    return [_set_result_content(msg, content) for msg in messages]


def _set_result_content(result: Any, content: Any) -> Any:
    if isinstance(result, dict):
        updated = dict(result)
        updated["content"] = content
        return updated
    if hasattr(result, "model_copy"):
        return result.model_copy(update={"content": content})
    if hasattr(result, "copy"):
        try:
            return result.copy(update={"content": content})
        except Exception:
            pass
    if hasattr(result, "content"):
        try:
            setattr(result, "content", content)
        except Exception:
            pass
    return result
