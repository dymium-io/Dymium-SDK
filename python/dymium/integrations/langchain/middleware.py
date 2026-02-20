"""LangChain middleware integration.

This middleware preserves Dymium's placeholder safety by:
- placeholderizing all messages before model calls
- resolving placeholders at tool boundaries
- re-sanitizing tool outputs before returning to the model
- exposing a deobfuscated final answer for the caller
"""
from __future__ import annotations

import contextvars
from typing import Any, Dict, List, Annotated, Callable

try:
    from typing_extensions import TypedDict
except Exception:  # pragma: no cover - fallback for older envs
    from typing import TypedDict  # type: ignore

from dymium.runtime.secure_runtime import DEFAULT_SYSTEM_PROMPT
from dymium.sanitization import Sanitizer, SanitizationContext, ensure_security_summary
from dymium.tools import TOOL_TYPE_AGENTIC, normalize_tool_type
from dataclasses import replace

try:
    from langgraph.graph import add_messages
except Exception:  # pragma: no cover
    def add_messages(a, b):  # type: ignore
        return (a or []) + (b or [])

try:
    from langchain_core.messages import RemoveMessage
    from langgraph.graph.message import REMOVE_ALL_MESSAGES
except Exception:  # pragma: no cover
    RemoveMessage = None  # type: ignore
    REMOVE_ALL_MESSAGES = None  # type: ignore

_AGENTIC_CONTEXT_STACK: contextvars.ContextVar[List[Dict[str, Any]] | None] = contextvars.ContextVar(
    "dymium_agentic_context_stack",
    default=None,
)


def _merge_placeholder_maps(left: Dict[str, str] | None, right: Dict[str, str] | None) -> Dict[str, str]:
    out: Dict[str, str] = {}
    out.update(left or {})
    out.update(right or {})
    return out


def _merge_type_counts(left: Dict[str, int] | None, right: Dict[str, int] | None) -> Dict[str, int]:
    out: Dict[str, int] = dict(left or {})
    for k, v in (right or {}).items():
        out[k] = out.get(k, 0) + int(v)
    return out


def _merge_tool_entity_rows(left: List[Dict[str, Any]] | None, right: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    return list(left or []) + list(right or [])


def _merge_tool_calls(left: List[Dict[str, Any]] | None, right: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    return list(left or []) + list(right or [])


def _merge_security_summaries(left: Dict[str, Any] | None, right: Dict[str, Any] | None) -> Dict[str, Any]:
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
    out["tool_usage"]["tool_calls"] = _merge_tool_calls(
        l_tool.get("tool_calls"),
        r_tool.get("tool_calls"),
    )
    out["tool_usage"]["sensitive_inputs_protected"] = bool(
        l_tool.get("sensitive_inputs_protected") or r_tool.get("sensitive_inputs_protected")
    )
    out["tool_usage"]["sensitive_outputs_protected"] = bool(
        l_tool.get("sensitive_outputs_protected") or r_tool.get("sensitive_outputs_protected")
    )
    out["tool_usage"]["entities_detected_in_tool_outputs"] = _merge_tool_entity_rows(
        l_tool.get("entities_detected_in_tool_outputs"),
        r_tool.get("entities_detected_in_tool_outputs"),
    )
    return out


def _max_int(left: int | None, right: int | None) -> int:
    return max(int(left or 0), int(right or 0))


class DymiumState(TypedDict, total=False):
    messages: Annotated[List[Any], add_messages]
    placeholder_map: Annotated[Dict[str, str], _merge_placeholder_maps]
    security_summary: Annotated[Dict[str, Any], _merge_security_summaries]
    text: str
    last_sanitized_index: Annotated[int, _max_int]


class DymiumSanitizer(Sanitizer):
    """Backward-compatible alias for Sanitizer."""
    pass


class DymiumMiddleware:  # runtime import of AgentMiddleware below
    def __init__(
        self,
        sanitizer: Sanitizer,
        system_prompt: str | None = DEFAULT_SYSTEM_PROMPT,
        tool_types: Dict[str, str] | None = None,
        trace_hook: Callable[[str, Dict[str, Any]], None] | None = None,
    ) -> None:
        self.sanitizer = sanitizer
        self.system_prompt = system_prompt
        self.tool_types = {str(k): normalize_tool_type(v) for k, v in (tool_types or {}).items()}
        self.trace_hook = trace_hook

        # Late import to keep langchain optional
        try:
            from langchain.agents.middleware import AgentMiddleware
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "LangChain is not installed. Install with: pip install langchain"
            ) from exc

        class _Impl(AgentMiddleware):
            state_schema = DymiumState

            def before_model(inner_self, state, runtime):  # type: ignore[override]
                return self._before_model(state)

            def wrap_tool_call(inner_self, request, handler):  # type: ignore[override]
                return self._wrap_tool_call(request, handler)

            def after_agent(inner_self, state, runtime):  # type: ignore[override]
                return self._after_agent(state)

        self._impl = _Impl()
        self.state_schema = self._impl.state_schema

    def middleware(self) -> Any:
        return self._impl

    def _emit_trace(self, event: str, payload: Dict[str, Any]) -> None:
        if not callable(self.trace_hook):
            return
        try:
            self.trace_hook(event, payload)
        except Exception:
            # Trace plumbing must never break agent execution.
            return

    def _before_model(self, state: Dict[str, Any]) -> Dict[str, Any]:
        messages = list(state.get("messages", []) or [])
        base_map = _read_placeholder_map(state)
        ambient = _peek_agentic_context()
        if isinstance(ambient, dict):
            if not base_map:
                base_map = _normalize_placeholder_map(ambient.get("placeholder_map"))
        ctx = SanitizationContext(
            placeholder_map=dict(base_map),
            security_summary=ensure_security_summary(),
        )

        inserted_system = False
        if self.system_prompt:
            updated_messages = _ensure_system_message(messages, self.system_prompt)
            inserted_system = updated_messages is not messages and len(updated_messages) == len(messages) + 1
            messages = updated_messages

        last_idx = state.get("last_sanitized_index", 0) or 0
        if inserted_system and last_idx > 0:
            last_idx += 1
        if last_idx < 0:
            last_idx = 0
        if last_idx > len(messages):
            last_idx = len(messages)

        prefix = messages[:last_idx]
        tail = messages[last_idx:]
        sanitized_tail = self.sanitizer.sanitize_messages(tail, ctx)
        sanitized = prefix + sanitized_tail
        last_idx = len(sanitized)
        map_full = dict(ctx.placeholder_map)
        map_delta = _map_delta(base_map, map_full)

        self._emit_trace(
            "before_model",
            {
                "placeholder_map": map_full,
                "placeholder_map_delta": map_delta,
                "security_summary": ctx.security_summary,
                "message_count": len(sanitized),
                "last_sanitized_index": last_idx,
            },
        )
        _update_agentic_context(map_full, None)

        return {
            # With add_messages reducer, replace message history explicitly.
            "messages": _replace_messages(sanitized),
            "placeholder_map": map_full,
            "security_summary": ctx.security_summary,
            "last_sanitized_index": last_idx,
        }

    def _wrap_tool_call(self, request: Any, handler: Any) -> Any:
        state = getattr(request, "state", None) or getattr(request, "get", lambda k, d=None: d)("state", {})
        base_map = _read_placeholder_map(state)
        ambient = _peek_agentic_context()
        if isinstance(ambient, dict):
            if not base_map:
                base_map = _normalize_placeholder_map(ambient.get("placeholder_map"))
        ctx = SanitizationContext(
            placeholder_map=dict(base_map),
            security_summary=ensure_security_summary(),
        )

        tool_call = getattr(request, "tool_call", None) or getattr(request, "get", lambda k, d=None: d)("tool_call", {})
        tool_name = _extract_tool_name(tool_call)
        tool_type = normalize_tool_type(self.tool_types.get(tool_name))
        tool_args = _extract_tool_args(tool_call)

        self.sanitizer.record_tool_call(tool_name, ctx)

        resolved_args = self.sanitizer.resolve_for_tool(tool_args, ctx, tool_type=tool_type)
        agentic_ctx = None
        if tool_type == TOOL_TYPE_AGENTIC and isinstance(resolved_args, dict):
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
        tool_call["arguments"] = resolved_args
        if tool_type == TOOL_TYPE_AGENTIC and isinstance(agentic_ctx, dict):
            tool_call["dymium_context"] = agentic_ctx

        self._emit_trace(
            "tool_call_start",
            {
                "tool_name": tool_name,
                "tool_type": tool_type,
                "llm_vision_args": tool_args,
                "resolved_args": resolved_args,
                "placeholder_map": dict(ctx.placeholder_map),
            },
        )

        request = _update_request_tool_call(request, tool_call)
        ambient_token: contextvars.Token | None = None
        if tool_type == TOOL_TYPE_AGENTIC and isinstance(agentic_ctx, dict):
            ambient_token = _push_agentic_context(agentic_ctx)
        try:
            result = handler(request)
        finally:
            if ambient_token is not None:
                ambient_after = _peek_agentic_context()
                if isinstance(ambient_after, dict) and isinstance(agentic_ctx, dict):
                    child_map = _normalize_placeholder_map(ambient_after.get("placeholder_map"))
                    if child_map:
                        agentic_ctx["placeholder_map"] = child_map
                    child_summary = ambient_after.get("security_summary")
                    if isinstance(child_summary, dict):
                        agentic_ctx["security_summary"] = ensure_security_summary(child_summary)
                _AGENTIC_CONTEXT_STACK.reset(ambient_token)

        if (
            tool_type == TOOL_TYPE_AGENTIC
            and isinstance(agentic_ctx, dict)
            and not _has_agentic_metadata(_tool_result_payload(result))
        ):
            child_map = _normalize_placeholder_map(agentic_ctx.get("placeholder_map"))
            if child_map:
                ctx.placeholder_map.update(child_map)
            child_summary = agentic_ctx.get("security_summary")
            if isinstance(child_summary, dict):
                child_summary = _scoped_child_summary(child_summary, parent_tool=tool_name)
                ctx.security_summary = _merge_security_summaries(ctx.security_summary, child_summary)

        sanitized_result = self.sanitizer.sanitize_tool_output(
            _tool_result_payload(result),
            ctx,
            tool_name,
            tool_type=tool_type,
        )
        _apply_sanitized_tool_result(result, sanitized_result)
        map_full = dict(ctx.placeholder_map)
        map_delta = _map_delta(base_map, map_full)
        tool_usage = ctx.security_summary.get("tool_usage") if isinstance(ctx.security_summary, dict) else {}
        output_entity_rows = list(tool_usage.get("entities_detected_in_tool_outputs") or []) if isinstance(tool_usage, dict) else []

        self._emit_trace(
            "tool_call_end",
            {
                "tool_name": tool_name,
                "tool_type": tool_type,
                "llm_vision_args": tool_args,
                "resolved_args": resolved_args,
                "sanitized_output": sanitized_result,
                "security_summary": ctx.security_summary,
                "output_entity_rows": output_entity_rows,
                "placeholder_map": map_full,
                "placeholder_map_delta": map_delta,
            },
        )
        _update_agentic_context(map_full, ctx.security_summary)

        # Persist placeholder_map and security_summary via Command update so state survives tool node merge.
        try:
            from langgraph.types import Command
        except Exception:
            Command = None  # type: ignore

        existing_count = 0
        if isinstance(state, dict):
            existing_count = len(state.get("messages", []) or [])

        if Command is not None and isinstance(result, Command):
            update = result.update
            if isinstance(update, dict):
                merged = dict(update)
                merged["placeholder_map"] = map_full
                merged["security_summary"] = ctx.security_summary
                if "messages" in merged and isinstance(merged["messages"], list):
                    merged["last_sanitized_index"] = existing_count + len(merged["messages"])
                return replace(result, update=merged)
            if isinstance(update, list):
                return Command(
                    graph=result.graph,
                    update={
                        "messages": update,
                        "placeholder_map": map_full,
                        "security_summary": ctx.security_summary,
                        "last_sanitized_index": existing_count + len(update),
                    },
                    resume=result.resume,
                    goto=result.goto,
                )
            return result

        if Command is not None:
            return Command(
                update={
                    "messages": [result],
                    "placeholder_map": map_full,
                    "security_summary": ctx.security_summary,
                    "last_sanitized_index": existing_count + 1,
                }
            )

        state["placeholder_map"] = map_full
        state["security_summary"] = ctx.security_summary
        return result

    def _after_agent(self, state: Dict[str, Any]) -> Dict[str, Any]:
        ctx = SanitizationContext(
            placeholder_map=_read_placeholder_map(state),
            security_summary=_read_security_summary(state),
        )
        messages = list(state.get("messages", []) or [])
        if not messages:
            return {}
        deob_messages = []
        for msg in messages:
            content = _get_message_content(msg)
            deob_messages.append(_set_message_content(msg, _deobfuscate_content(content, self.sanitizer, ctx)))

        updates: Dict[str, Any] = {"messages": deob_messages}
        state["messages"] = deob_messages
        last = deob_messages[-1]
        content = _get_message_content(last)
        if isinstance(content, str):
            updates["text"] = content
        return updates


def _ensure_system_message(messages: List[Any], system_prompt: str) -> List[Any]:
    # Keep exactly one canonical security system prompt at the start.
    anchor = (system_prompt.splitlines() or [""])[0].strip()
    filtered = [
        msg for msg in messages
        if not _system_message_matches(msg, system_prompt, anchor)
    ]
    system_message = _make_system_message(system_prompt)
    return [system_message, *filtered]


def _is_system_message(msg: Any) -> bool:
    if isinstance(msg, dict):
        return msg.get("role") == "system"
    role = getattr(msg, "type", None) or getattr(msg, "role", None)
    return role == "system"


def _system_message_matches(msg: Any, system_prompt: str, anchor: str) -> bool:
    if not _is_system_message(msg):
        return False

    content: Any = ""
    if isinstance(msg, dict):
        content = msg.get("content") or ""
    elif hasattr(msg, "content"):
        content = getattr(msg, "content")

    if isinstance(content, str):
        text = content.strip()
        return text == system_prompt.strip() or (anchor and text.startswith(anchor))
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        text = "\n".join(parts).strip()
        return text == system_prompt.strip() or (anchor and text.startswith(anchor))
    return False


def _make_system_message(content: str) -> Any:
    try:
        from langchain_core.messages import SystemMessage
        return SystemMessage(content=content)
    except Exception:
        return {"role": "system", "content": content}


def _get_message_content(msg: Any) -> Any:
    if isinstance(msg, dict):
        return msg.get("content") or ""
    if hasattr(msg, "content"):
        return getattr(msg, "content")
    return ""


def _set_message_content(msg: Any, content: Any) -> Any:
    if isinstance(msg, dict):
        updated = dict(msg)
        updated["content"] = content
        return updated
    if hasattr(msg, "model_copy"):
        return msg.model_copy(update={"content": content})
    if hasattr(msg, "copy"):
        try:
            return msg.copy(update={"content": content})
        except Exception:
            pass
    if hasattr(msg, "content"):
        try:
            setattr(msg, "content", content)
        except Exception:
            pass
    return msg


def _deobfuscate_content(content: Any, sanitizer: Sanitizer, ctx: SanitizationContext) -> Any:
    if isinstance(content, str):
        return sanitizer.deobfuscate(content, ctx)
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict):
                new_part = dict(part)
                if isinstance(new_part.get("text"), str):
                    new_part["text"] = sanitizer.deobfuscate(new_part["text"], ctx)
                out.append(new_part)
                continue
            out.append(part)
        return out
    return content


def _extract_tool_name(tool_call: Any) -> str | None:
    if isinstance(tool_call, dict):
        return tool_call.get("name") or tool_call.get("function", {}).get("name")
    return None


def _extract_tool_args(tool_call: Any) -> Any:
    if isinstance(tool_call, dict):
        return tool_call.get("args") or tool_call.get("arguments") or {}
    return {}


def _update_request_tool_call(request: Any, tool_call: Dict[str, Any]) -> Any:
    if hasattr(request, "override"):
        try:
            return request.override(tool_call=tool_call)
        except Exception:
            pass
    if hasattr(request, "model_copy"):
        return request.model_copy(update={"tool_call": tool_call})
    if hasattr(request, "copy"):
        try:
            return request.copy(update={"tool_call": tool_call})
        except Exception:
            pass
    if isinstance(request, dict):
        updated = dict(request)
        updated["tool_call"] = tool_call
        return updated
    if hasattr(request, "tool_call"):
        try:
            setattr(request, "tool_call", tool_call)
        except Exception:
            pass
    return request


def _tool_result_payload(result: Any) -> Any:
    if hasattr(result, "content"):
        return getattr(result, "content")
    if isinstance(result, dict) and "content" in result:
        return result["content"]
    return result


def _apply_sanitized_tool_result(result: Any, sanitized: Any) -> None:
    if hasattr(result, "content"):
        try:
            setattr(result, "content", sanitized)
            return
        except Exception:
            pass
    if isinstance(result, dict):
        result["content"] = sanitized
        return
        # If tool returns Command(update={messages:[...]}), merge placeholder state.
    if hasattr(result, "update") and isinstance(result.update, dict):
        msgs = result.update.get("messages") or []
        new_msgs = []
        for msg in msgs:
            new_msgs.append(_set_message_content(msg, sanitized))
        result.update["messages"] = new_msgs


def _map_delta(base: Dict[str, str], updated: Dict[str, str]) -> Dict[str, str]:
    delta: Dict[str, str] = {}
    for k, v in updated.items():
        if base.get(k) != v:
            delta[k] = v
    return delta


def _replace_messages(messages: List[Any]) -> List[Any]:
    if RemoveMessage is not None and REMOVE_ALL_MESSAGES is not None:
        return [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages]
    return messages


def _read_placeholder_map(state: Dict[str, Any]) -> Dict[str, str]:
    return _normalize_placeholder_map(
        state.get("placeholder_map") if isinstance(state, dict) else None
    ) or _normalize_placeholder_map(
        state.get("placeholderMap") if isinstance(state, dict) else None
    )


def _read_security_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(state, dict):
        raw = state.get("security_summary")
        if isinstance(raw, dict):
            return ensure_security_summary(raw)
        raw = state.get("securitySummary")
        if isinstance(raw, dict):
            return ensure_security_summary(raw)
    return ensure_security_summary()


def _normalize_placeholder_map(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out = {str(k): str(v) for k, v in raw.items()}
    for key in list(out.keys()):
        seen: set[str] = set()
        value = out.get(key, "")
        while value in out and value not in seen:
            seen.add(value)
            value = out.get(value, value)
        out[key] = value
    return out


def _has_agentic_metadata(value: Any) -> bool:
    if isinstance(value, dict):
        if any(k in value for k in ("placeholder_map", "placeholderMap", "security_summary", "securitySummary")):
            return True
        return any(_has_agentic_metadata(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_agentic_metadata(item) for item in value)
    return False


def _scoped_child_summary(summary: Dict[str, Any], parent_tool: str | None) -> Dict[str, Any]:
    out = ensure_security_summary(summary)
    if not parent_tool:
        return out

    usage = out.get("tool_usage")
    if not isinstance(usage, dict):
        return out
    calls = usage.get("tool_calls")
    if not isinstance(calls, list):
        return out

    scoped_prefix = f"{parent_tool}.subagent"
    for call in calls:
        if not isinstance(call, dict):
            continue
        if not call.get("parent_tool"):
            call["parent_tool"] = parent_tool
        scope = str(call.get("scope") or "root")
        if scope == "root":
            call["scope"] = scoped_prefix
        elif scope.startswith("root."):
            call["scope"] = scope.replace("root", scoped_prefix, 1)
    return out


def _push_agentic_context(payload: Dict[str, Any]) -> contextvars.Token:
    stack = list(_AGENTIC_CONTEXT_STACK.get() or [])
    frame = {
        "placeholder_map": _normalize_placeholder_map(payload.get("placeholder_map")),
        "security_summary": ensure_security_summary(payload.get("security_summary")),
    }
    stack.append(frame)
    return _AGENTIC_CONTEXT_STACK.set(stack)


def _peek_agentic_context() -> Dict[str, Any] | None:
    stack = _AGENTIC_CONTEXT_STACK.get() or []
    if not stack:
        return None
    top = stack[-1]
    if not isinstance(top, dict):
        return None
    return top


def _update_agentic_context(placeholder_map: Dict[str, str] | None, security_summary: Dict[str, Any] | None) -> None:
    stack = _AGENTIC_CONTEXT_STACK.get() or []
    if not stack:
        return
    current = stack[-1]
    if not isinstance(current, dict):
        return

    current_map = _normalize_placeholder_map(current.get("placeholder_map"))
    if isinstance(placeholder_map, dict):
        current_map = _merge_placeholder_maps(current_map, _normalize_placeholder_map(placeholder_map))
    current["placeholder_map"] = current_map

    current_summary = ensure_security_summary(current.get("security_summary"))
    if isinstance(security_summary, dict):
        current_summary = _merge_security_summaries(current_summary, ensure_security_summary(security_summary))
    current["security_summary"] = current_summary
