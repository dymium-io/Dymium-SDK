"""LangGraph state helpers."""
from __future__ import annotations

from typing import Any, Dict, List, Annotated

from dymium.runtime.secure_runtime import DEFAULT_SYSTEM_PROMPT
from dymium.sanitization import Sanitizer, SanitizationContext, ensure_security_summary

try:
    from typing_extensions import TypedDict
except Exception:  # pragma: no cover
    from typing import TypedDict  # type: ignore

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


class DymiumMessagesState(TypedDict, total=False):
    messages: Annotated[List[Any], add_messages]
    placeholder_map: Annotated[Dict[str, str], _merge_placeholder_maps]
    security_summary: Annotated[Dict[str, Any], _merge_security_summaries]
    last_sanitized_index: Annotated[int, _max_int]


def sanitize_state_messages(
    state: Dict[str, Any],
    sanitizer: Sanitizer,
    *,
    system_prompt: str | None = DEFAULT_SYSTEM_PROMPT,
    messages_key: str = "messages",
) -> Dict[str, Any]:
    messages = list(state.get(messages_key, []) or [])
    base_map = _read_placeholder_map(state)
    ctx = SanitizationContext(
        placeholder_map=dict(base_map),
        security_summary=ensure_security_summary(),
    )

    inserted_system = False
    if system_prompt:
        updated_messages = _ensure_system_message(messages, system_prompt)
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
    sanitized_tail = sanitizer.sanitize_messages(tail, ctx)
    sanitized = prefix + sanitized_tail
    last_idx = len(sanitized)

    return {
        # With add_messages reducer, replace message history explicitly.
        messages_key: _replace_messages(sanitized),
        "placeholder_map": dict(ctx.placeholder_map),
        "security_summary": ctx.security_summary,
        "last_sanitized_index": last_idx,
    }


def deobfuscate_last_message(
    state: Dict[str, Any],
    sanitizer: Sanitizer,
    *,
    messages_key: str = "messages",
) -> Dict[str, Any]:
    messages = list(state.get(messages_key, []) or [])
    if not messages:
        return {}
    deob_messages = _deobfuscate_messages(messages, state, sanitizer)
    if isinstance(state, dict):
        state[messages_key] = deob_messages
    ctx = SanitizationContext(
        placeholder_map=_read_placeholder_map(state),
        security_summary=ensure_security_summary(state.get("security_summary")),
    )
    last = deob_messages[-1]
    content = _get_message_content(last)
    if isinstance(content, str):
        return {"text": sanitizer.deobfuscate(content, ctx)}
    return {}


def deobfuscate_state_messages(
    state: Dict[str, Any],
    sanitizer: Sanitizer,
    *,
    messages_key: str = "messages",
) -> Dict[str, Any]:
    messages = list(state.get(messages_key, []) or [])
    if not messages:
        return {}
    return {messages_key: _deobfuscate_messages(messages, state, sanitizer)}


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
    if not isinstance(state, dict):
        return {}
    raw = state.get("placeholder_map")
    if not isinstance(raw, dict):
        raw = state.get("placeholderMap")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _deobfuscate_messages(
    messages: List[Any],
    state: Dict[str, Any],
    sanitizer: Sanitizer,
) -> List[Any]:
    ctx = SanitizationContext(
        placeholder_map=dict(state.get("placeholder_map") or {}),
        security_summary=ensure_security_summary(state.get("security_summary")),
    )
    out: List[Any] = []
    for msg in messages:
        content = _get_message_content(msg)
        out.append(_set_message_content(msg, _deobfuscate_content(content, sanitizer, ctx)))
    return out


def _deobfuscate_content(content: Any, sanitizer: Sanitizer, ctx: SanitizationContext) -> Any:
    if isinstance(content, str):
        return sanitizer.deobfuscate(content, ctx)
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict):
                updated = dict(part)
                if isinstance(updated.get("text"), str):
                    updated["text"] = sanitizer.deobfuscate(updated["text"], ctx)
                out.append(updated)
                continue
            out.append(part)
        return out
    return content
