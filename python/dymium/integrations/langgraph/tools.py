"""LangGraph tool integration."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Dict, Sequence

from dymium.sanitization import Sanitizer, SanitizationContext, ensure_security_summary
from dymium.tools import TOOL_TYPE_DELEGATED, normalize_direct_input_mode, normalize_tool_type


def make_tool_call_wrapper(
    sanitizer: Sanitizer,
    *,
    messages_key: str = "messages",
    tool_types: Dict[str, str] | None = None,
    tool_direct_input_modes: Dict[str, str] | None = None,
) -> Callable[[Any, Callable[[Any], Any]], Any]:
    normalized_tool_types = {
        str(k): normalize_tool_type(v)
        for k, v in (tool_types or {}).items()
    }
    normalized_direct_input_modes = {
        str(k): normalize_direct_input_mode(v)
        for k, v in (tool_direct_input_modes or {}).items()
    }

    def wrap_tool_call(request: Any, handler: Callable[[Any], Any]) -> Any:
        state = request.state if hasattr(request, "state") else {}
        base_map = _read_placeholder_map(state)
        ctx = SanitizationContext(
            placeholder_map=dict(base_map),
            security_summary=ensure_security_summary(),
        )

        tool_call = request.tool_call
        tool_name = tool_call.get("name") if isinstance(tool_call, dict) else None
        tool_args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
        tool_type = normalize_tool_type(normalized_tool_types.get(tool_name))
        direct_input_mode = normalize_direct_input_mode(
            normalized_direct_input_modes.get(tool_name)
        )

        sanitizer.record_tool_call(tool_name, ctx)

        resolved_args = sanitizer.resolve_for_tool(
            tool_args,
            ctx,
            tool_type=tool_type,
            direct_input_mode=direct_input_mode,
        )
        agentic_ctx: Dict[str, Any] | None = None
        if (
            tool_type == TOOL_TYPE_DELEGATED
            and isinstance(resolved_args, dict)
        ):
            resolved_args = dict(resolved_args)
            existing_ctx = resolved_args.get("dymium_context")
            agentic_ctx = dict(existing_ctx) if isinstance(existing_ctx, dict) else {}
            if "placeholder_map" not in agentic_ctx:
                agentic_ctx["placeholder_map"] = dict(ctx.placeholder_map)
            if "security_summary" not in agentic_ctx:
                agentic_ctx["security_summary"] = ensure_security_summary()
            resolved_args["dymium_context"] = agentic_ctx
        tool_call = dict(tool_call)
        tool_call["args"] = resolved_args
        if tool_type == TOOL_TYPE_DELEGATED and isinstance(agentic_ctx, dict):
            tool_call["dymium_context"] = agentic_ctx

        if hasattr(request, "override"):
            request = request.override(tool_call=tool_call)

        result = handler(request)
        if (
            tool_type == TOOL_TYPE_DELEGATED
            and isinstance(agentic_ctx, dict)
            and not _has_agentic_metadata(result)
        ):
            child_map = _normalize_placeholder_map(agentic_ctx.get("placeholder_map"))
            if child_map:
                ctx.placeholder_map.update(child_map)
            child_summary = agentic_ctx.get("security_summary")
            if isinstance(child_summary, dict):
                ctx.security_summary = _merge_security_summaries(
                    ctx.security_summary,
                    child_summary,
                    parent_tool=tool_name,
                )

        sanitized_result = sanitizer.sanitize_tool_output(
            _tool_result_payload(result),
            ctx,
            tool_name,
            tool_type=tool_type,
        )

        map_full = dict(ctx.placeholder_map)
        return _merge_tool_result(result, sanitized_result, map_full, ctx, state, messages_key)

    return wrap_tool_call


def make_tool_node(
    tools: Sequence[Any],
    sanitizer: Sanitizer,
    *,
    messages_key: str = "messages",
    tool_types: Dict[str, str] | None = None,
    tool_direct_input_modes: Dict[str, str] | None = None,
    **kwargs: Any,
):
    try:
        from langgraph.prebuilt import ToolNode
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("LangGraph is not installed. Install with: pip install langgraph") from exc

    wrap = make_tool_call_wrapper(
        sanitizer,
        messages_key=messages_key,
        tool_types=tool_types,
        tool_direct_input_modes=tool_direct_input_modes,
    )
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
    map_full: Dict[str, str],
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
            merged["placeholder_map"] = map_full
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
                    "placeholder_map": map_full,
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
                "placeholder_map": map_full,
                "security_summary": ctx.security_summary,
                "last_sanitized_index": existing_count + 1,
            }
        )

    # Fallback: mutate state
    if isinstance(state, dict):
        state["placeholder_map"] = map_full
        state["security_summary"] = ctx.security_summary
        state["last_sanitized_index"] = existing_count + 1
    return _set_result_content(result, sanitized)


def _set_messages_content(messages: list[Any], content: Any) -> list[Any]:
    return [_set_result_content(msg, content) for msg in messages]


def _merge_type_counts(left: Dict[str, int] | None, right: Dict[str, int] | None) -> Dict[str, int]:
    out: Dict[str, int] = dict(left or {})
    for k, v in (right or {}).items():
        out[str(k)] = out.get(str(k), 0) + int(v)
    return out


def _merge_security_summaries(
    left: Dict[str, Any] | None,
    right: Dict[str, Any] | None,
    *,
    parent_tool: str | None = None,
) -> Dict[str, Any]:
    l = ensure_security_summary(left)
    r = ensure_security_summary(right)
    out = ensure_security_summary()

    l_in = l.get("input_redaction", {})
    r_in = r.get("input_redaction", {})
    out["input_redaction"]["sensitive_detected"] = bool(
        l_in.get("sensitive_detected") or r_in.get("sensitive_detected")
    )
    out["input_redaction"]["entities_detected"] = {
        "count": int((l_in.get("entities_detected") or {}).get("count", 0))
        + int((r_in.get("entities_detected") or {}).get("count", 0)),
        "types": _merge_type_counts(
            (l_in.get("entities_detected") or {}).get("types"),
            (r_in.get("entities_detected") or {}).get("types"),
        ),
    }

    l_tool = l.get("tool_usage", {})
    r_tool = r.get("tool_usage", {})
    out["tool_usage"]["tool_calls"] = list(l_tool.get("tool_calls") or []) + _attach_parent_to_tool_calls(
        r_tool.get("tool_calls"),
        parent_tool=parent_tool,
    )
    out["tool_usage"]["sensitive_inputs_protected"] = bool(
        l_tool.get("sensitive_inputs_protected") or r_tool.get("sensitive_inputs_protected")
    )
    out["tool_usage"]["sensitive_outputs_protected"] = bool(
        l_tool.get("sensitive_outputs_protected") or r_tool.get("sensitive_outputs_protected")
    )
    out["tool_usage"]["entities_detected_in_tool_outputs"] = list(
        l_tool.get("entities_detected_in_tool_outputs") or []
    ) + list(
        r_tool.get("entities_detected_in_tool_outputs") or []
    )
    return out


def _attach_parent_to_tool_calls(
    calls: Any,
    *,
    parent_tool: str | None = None,
) -> list[Dict[str, Any]]:
    out: list[Dict[str, Any]] = []
    if not isinstance(calls, list):
        return out
    for call in calls:
        if not isinstance(call, dict):
            continue
        name = call.get("name")
        if not isinstance(name, str) or not name:
            continue
        merged = dict(call)
        if parent_tool and not merged.get("parent_tool"):
            merged["parent_tool"] = parent_tool
            merged["scope"] = f"{parent_tool}.subagent"
        else:
            merged["scope"] = merged.get("scope") or "root"
            merged["parent_tool"] = merged.get("parent_tool")
        out.append(merged)
    return out


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


def _read_placeholder_map(state: Any) -> Dict[str, str]:
    getter = getattr(state, "get", None)
    if not callable(getter):
        return {}
    raw = getter("placeholder_map", None)
    if not isinstance(raw, dict):
        raw = getter("placeholderMap", None)
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _normalize_placeholder_map(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _has_agentic_metadata(value: Any) -> bool:
    if isinstance(value, dict):
        if any(k in value for k in ("placeholder_map", "placeholderMap", "security_summary", "securitySummary")):
            return True
        return any(_has_agentic_metadata(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_agentic_metadata(item) for item in value)
    return False
