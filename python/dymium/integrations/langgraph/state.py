"""LangGraph state helpers."""
from __future__ import annotations

from typing import Any, Dict, List

from dymium.runtime.secure_runtime import DEFAULT_SYSTEM_PROMPT
from dymium.sanitization import Sanitizer, SanitizationContext, ensure_security_summary


def sanitize_state_messages(
    state: Dict[str, Any],
    sanitizer: Sanitizer,
    *,
    system_prompt: str | None = DEFAULT_SYSTEM_PROMPT,
    pii_options: Dict[str, Any] | None = None,
    messages_key: str = "messages",
) -> Dict[str, Any]:
    messages = list(state.get(messages_key, []) or [])
    ctx = SanitizationContext(
        placeholder_map=dict(state.get("placeholder_map") or {}),
        security_summary=ensure_security_summary(state.get("security_summary")),
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
    sanitized_tail = sanitizer.sanitize_messages(tail, ctx, pii_options)
    sanitized = prefix + sanitized_tail
    last_idx = len(sanitized)

    return {
        messages_key: sanitized,
        "placeholder_map": ctx.placeholder_map,
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
    ctx = SanitizationContext(
        placeholder_map=dict(state.get("placeholder_map") or {}),
        security_summary=ensure_security_summary(state.get("security_summary")),
    )
    last = messages[-1]
    content = _get_message_content(last)
    if isinstance(content, str):
        return {"text_deobfuscated": sanitizer.deobfuscate(content, ctx)}
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
