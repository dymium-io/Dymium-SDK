"""LangChain middleware integration.

This middleware preserves Dymium's placeholder safety by:
- placeholderizing all messages before model calls
- resolving placeholders at tool boundaries
- re-sanitizing tool outputs before returning to the model
- exposing a deobfuscated final answer for the caller
"""
from __future__ import annotations

from typing import Any, Dict, List

try:
    from typing_extensions import TypedDict, NotRequired
except Exception:  # pragma: no cover - fallback for older envs
    from typing import TypedDict  # type: ignore
    NotRequired = Any  # type: ignore

from dymium.runtime.secure_runtime import DEFAULT_SYSTEM_PROMPT
from dymium.sanitization import Sanitizer, SanitizationContext, ensure_security_summary
from dataclasses import replace


try:  # Prefer LangChain's AgentState to keep message merge semantics.
    from langchain.agents import AgentState

    class DymiumState(AgentState):
        placeholder_map: NotRequired[Dict[str, str]]
        security_summary: NotRequired[Dict[str, Any]]
        text_deobfuscated: NotRequired[str]
        last_sanitized_index: NotRequired[int]

except Exception:  # pragma: no cover - fallback without langchain import

    class DymiumState(TypedDict, total=False):
        messages: List[Any]
        placeholder_map: Dict[str, str]
        security_summary: Dict[str, Any]
        text_deobfuscated: str
        last_sanitized_index: int


class DymiumSanitizer(Sanitizer):
    """Backward-compatible alias for Sanitizer."""
    pass


class DymiumMiddleware:  # runtime import of AgentMiddleware below
    def __init__(
        self,
        sanitizer: Sanitizer,
        system_prompt: str | None = DEFAULT_SYSTEM_PROMPT,
        pii_options: Dict[str, Any] | None = None,
    ) -> None:
        self.sanitizer = sanitizer
        self.system_prompt = system_prompt
        self.pii_options = pii_options

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

    def _before_model(self, state: Dict[str, Any]) -> Dict[str, Any]:
        messages = list(state.get("messages", []) or [])
        ctx = SanitizationContext(
            placeholder_map=dict(state.get("placeholder_map") or {}),
            security_summary=ensure_security_summary(state.get("security_summary")),
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
        sanitized_tail = self.sanitizer.sanitize_messages(tail, ctx, self.pii_options)
        sanitized = prefix + sanitized_tail
        last_idx = len(sanitized)

        return {
            "messages": sanitized,
            "placeholder_map": ctx.placeholder_map,
            "security_summary": ctx.security_summary,
            "last_sanitized_index": last_idx,
        }

    def _wrap_tool_call(self, request: Any, handler: Any) -> Any:
        state = getattr(request, "state", None) or getattr(request, "get", lambda k, d=None: d)("state", {})
        ctx = SanitizationContext(
            placeholder_map=dict(state.get("placeholder_map") or {}),
            security_summary=ensure_security_summary(state.get("security_summary")),
        )

        tool_call = getattr(request, "tool_call", None) or getattr(request, "get", lambda k, d=None: d)("tool_call", {})
        tool_name = _extract_tool_name(tool_call)
        tool_args = _extract_tool_args(tool_call)

        self.sanitizer.record_tool_call(tool_name, ctx)

        resolved_args = self.sanitizer.resolve_for_tool(tool_args, ctx)
        tool_call = dict(tool_call)
        tool_call["args"] = resolved_args
        tool_call["arguments"] = resolved_args

        request = _update_request_tool_call(request, tool_call)
        result = handler(request)

        sanitized_result = self.sanitizer.sanitize_tool_output(
            _tool_result_payload(result),
            ctx,
            self.pii_options,
            tool_name,
        )
        _apply_sanitized_tool_result(result, sanitized_result)

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
                merged["placeholder_map"] = ctx.placeholder_map
                merged["security_summary"] = ctx.security_summary
                if "messages" in merged and isinstance(merged["messages"], list):
                    merged["last_sanitized_index"] = existing_count + len(merged["messages"])
                return replace(result, update=merged)
            if isinstance(update, list):
                return Command(
                    graph=result.graph,
                    update={
                        "messages": update,
                        "placeholder_map": ctx.placeholder_map,
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
                    "placeholder_map": ctx.placeholder_map,
                    "security_summary": ctx.security_summary,
                    "last_sanitized_index": existing_count + 1,
                }
            )

        state["placeholder_map"] = ctx.placeholder_map
        state["security_summary"] = ctx.security_summary
        return result

    def _after_agent(self, state: Dict[str, Any]) -> Dict[str, Any]:
        ctx = SanitizationContext(
            placeholder_map=dict(state.get("placeholder_map") or {}),
            security_summary=ensure_security_summary(state.get("security_summary")),
        )
        messages = list(state.get("messages", []) or [])
        if not messages:
            return {}
        last = messages[-1]
        content = _get_message_content(last)
        if isinstance(content, str):
            return {"text_deobfuscated": self.sanitizer.deobfuscate(content, ctx)}
        return {}


def _ensure_system_message(messages: List[Any], system_prompt: str) -> List[Any]:
    if messages:
        first = messages[0]
        if _is_system_message(first):
            return messages
    system_message = _make_system_message(system_prompt)
    return [system_message, *messages]


def _is_system_message(msg: Any) -> bool:
    if isinstance(msg, dict):
        return msg.get("role") == "system"
    role = getattr(msg, "type", None) or getattr(msg, "role", None)
    return role == "system"


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
