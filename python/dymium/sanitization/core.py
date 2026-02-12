"""Framework-agnostic sanitization module.

This module provides a reusable placeholder boundary that can be integrated
into any orchestration framework.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Tuple

from dymium.core.pii import PIIEngine
from dymium.core.redaction import RedactionEngineProtocol
from dymium.redaction import RedactionEngine


def ensure_security_summary(summary: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if summary:
        return summary
    return {
        "input_redaction": {
            "entities_detected": {"count": 0, "types": {}},
            "sensitive_detected": False,
        },
        "tool_usage": {
            "tools_called": [],
            "tool_calls_count": 0,
            "sensitive_inputs_protected": False,
            "sensitive_outputs_protected": False,
            "entities_detected_in_tool_outputs": [],
        },
    }


@dataclass
class SanitizationContext:
    placeholder_map: Dict[str, str] = field(default_factory=dict)
    security_summary: Dict[str, Any] = field(default_factory=ensure_security_summary)


class Sanitizer:
    def __init__(self, pii: PIIEngine, redaction: RedactionEngineProtocol | None = None) -> None:
        self.pii = pii
        self.redaction = redaction or RedactionEngine()

    def sanitize_text(
        self,
        text: str,
        ctx: SanitizationContext,
        pii_options: Dict[str, Any] | None = None,
    ) -> str:
        if not text:
            return ""
        entities = list(self.pii.detect(text, options=pii_options))
        summary = _summarize_entities(entities)
        _merge_entity_summary(ctx.security_summary["input_redaction"]["entities_detected"], summary)
        if summary.get("count", 0) > 0:
            ctx.security_summary["input_redaction"]["sensitive_detected"] = True
        if not entities:
            return text
        redacted = self.redaction.placeholderize(text, entities, ctx.placeholder_map)
        ctx.placeholder_map = redacted.get("placeholder_map", ctx.placeholder_map)
        return redacted.get("text", text)

    def sanitize_messages(
        self,
        messages: Iterable[Any],
        ctx: SanitizationContext,
        pii_options: Dict[str, Any] | None = None,
    ) -> List[Any]:
        sanitized: List[Any] = []
        for msg in messages:
            content = _get_message_content(msg)
            new_content = _sanitize_message_content(content, ctx, self, pii_options)
            sanitized.append(_set_message_content(msg, new_content))
        return sanitized

    def resolve_for_tool(self, args: Any, ctx: SanitizationContext) -> Any:
        if _contains_placeholders(args, ctx.placeholder_map):
            ctx.security_summary["tool_usage"]["sensitive_inputs_protected"] = True
        return _resolve_obj(args, ctx.placeholder_map, self.redaction)

    def sanitize_tool_output(
        self,
        tool_output: Any,
        ctx: SanitizationContext,
        pii_options: Dict[str, Any] | None = None,
        tool_name: str | None = None,
    ) -> Any:
        sanitized, summary = _sanitize_tool_output(
            tool_output,
            ctx.placeholder_map,
            self,
            pii_options,
            tool_name,
        )
        if summary:
            ctx.security_summary["tool_usage"]["sensitive_outputs_protected"] = True
            ctx.security_summary["tool_usage"]["entities_detected_in_tool_outputs"].append(summary)
        return sanitized

    def deobfuscate(self, text: str, ctx: SanitizationContext) -> str:
        return self.redaction.resolve_placeholders(text, ctx.placeholder_map)

    def record_tool_call(self, tool_name: str | None, ctx: SanitizationContext) -> None:
        if not tool_name:
            return
        ctx.security_summary["tool_usage"]["tools_called"].append(tool_name)
        ctx.security_summary["tool_usage"]["tool_calls_count"] += 1


def _summarize_entities(entities: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    summary = {"count": 0, "types": {}}
    for ent in entities:
        if not isinstance(ent, dict):
            continue
        summary["count"] += 1
        etype = ent.get("type") or "VALUE"
        summary["types"][str(etype)] = summary["types"].get(str(etype), 0) + 1
    return summary


def _merge_entity_summary(target: Dict[str, Any], delta: Dict[str, Any]) -> None:
    target["count"] = target.get("count", 0) + delta.get("count", 0)
    types = target.get("types", {})
    for k, v in (delta.get("types") or {}).items():
        types[k] = types.get(k, 0) + v
    target["types"] = types


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


def _sanitize_message_content(
    content: Any,
    ctx: SanitizationContext,
    sanitizer: Sanitizer,
    pii_options: Dict[str, Any] | None,
) -> Any:
    if isinstance(content, str):
        return sanitizer.sanitize_text(content, ctx, pii_options)
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                new_text = sanitizer.sanitize_text(part.get("text", ""), ctx, pii_options)
                new_part = dict(part)
                new_part["text"] = new_text
                parts.append(new_part)
            else:
                parts.append(part)
        return parts
    return content


def _resolve_obj(obj: Any, placeholder_map: Dict[str, str], redaction: RedactionEngine) -> Any:
    if isinstance(obj, dict):
        return {k: _resolve_obj(v, placeholder_map, redaction) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_obj(v, placeholder_map, redaction) for v in obj]
    if isinstance(obj, str):
        return redaction.resolve_placeholders(obj, placeholder_map)
    return obj


def _sanitize_tool_output(
    tool_output: Any,
    placeholder_map: Dict[str, str],
    sanitizer: Sanitizer,
    pii_options: Dict[str, Any] | None,
    tool_name: str | None,
) -> Tuple[Any, Dict[str, Any] | None]:
    if isinstance(tool_output, dict):
        sanitized = {}
        tool_summary = None
        for k, v in tool_output.items():
            sanitized_value, summary = _sanitize_tool_output(
                v,
                placeholder_map,
                sanitizer,
                pii_options,
                tool_name,
            )
            sanitized[k] = sanitized_value
            if summary:
                tool_summary = summary
        return sanitized, tool_summary
    if isinstance(tool_output, list):
        items = []
        tool_summary = None
        for item in tool_output:
            sanitized_value, summary = _sanitize_tool_output(
                item,
                placeholder_map,
                sanitizer,
                pii_options,
                tool_name,
            )
            items.append(sanitized_value)
            if summary:
                tool_summary = summary
        return items, tool_summary
    if isinstance(tool_output, str):
        if not tool_output:
            return tool_output, None
        entities = list(sanitizer.pii.detect(tool_output, options=pii_options))
        if not entities:
            return tool_output, None
        redacted = sanitizer.redaction.placeholderize(tool_output, entities, placeholder_map)
        summary = _summarize_entities(entities)
        if tool_name:
            summary = {"tool": tool_name, **summary}
        return redacted.get("text", tool_output), summary
    return tool_output, None


def _contains_placeholders(obj: Any, placeholder_map: Dict[str, str]) -> bool:
    if not placeholder_map:
        return False
    if isinstance(obj, dict):
        return any(_contains_placeholders(v, placeholder_map) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_placeholders(v, placeholder_map) for v in obj)
    if isinstance(obj, str):
        return any(ph in obj for ph in placeholder_map.keys())
    return False
