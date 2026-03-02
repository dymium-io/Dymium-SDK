"""Framework-agnostic sanitization module.

This module provides a reusable placeholder boundary that can be integrated
into any orchestration framework.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
import json
import re
from typing import Any, Dict, Iterable, List, Tuple

from dymium.core.pii import PIIEngine
from dymium.core.redaction import RedactionEngineProtocol
from dymium.redaction import RedactionEngine
from dymium.tools import TOOL_TYPE_DELEGATED, normalize_tool_type, should_resolve_tool_inputs


PLACEHOLDER_RE = re.compile(r"\bPH_[A-Z]+_[A-Z0-9]{5}\b")


def ensure_security_summary(summary: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if summary:
        return summary
    return {
        "input_redaction": {
            "entities_detected": {"count": 0, "types": {}},
            "sensitive_detected": False,
        },
        "tool_usage": {
            "tool_calls": [],
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
    ) -> str:
        if not text:
            return ""
        entities = _drop_placeholder_entities(
            _coerce_entities(self.pii.normalize(self.pii.detect(text))),
            text,
        )
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
    ) -> List[Any]:
        sanitized: List[Any] = []
        for msg in messages:
            content = _get_message_content(msg)
            new_content = _sanitize_message_content(content, ctx, self)
            sanitized.append(_set_message_content(msg, new_content))
        return sanitized

    def resolve_for_tool(
        self,
        args: Any,
        ctx: SanitizationContext,
        *,
        tool_type: str | None = None,
        direct_input_mode: str | None = None,
    ) -> Any:
        if _contains_placeholders(args, ctx.placeholder_map):
            ctx.security_summary["tool_usage"]["sensitive_inputs_protected"] = True
        if not should_resolve_tool_inputs(
            tool_type=normalize_tool_type(tool_type),
            direct_input_mode=direct_input_mode,
        ):
            return args
        return _resolve_obj(args, ctx.placeholder_map, self.redaction)

    def sanitize_tool_output(
        self,
        tool_output: Any,
        ctx: SanitizationContext,
        tool_name: str | None = None,
        *,
        tool_type: str | None = None,
    ) -> Any:
        if normalize_tool_type(tool_type) == TOOL_TYPE_DELEGATED:
            tool_output, placeholder_updates, child_security_summary = _extract_agentic_metadata_from_output(tool_output)
            if placeholder_updates:
                ctx.placeholder_map.update(placeholder_updates)
            if child_security_summary:
                _merge_security_summary(ctx.security_summary, child_security_summary, parent_tool=tool_name)
        sanitized, summary = _sanitize_tool_output(
            tool_output,
            ctx.placeholder_map,
            self,
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
        tool_usage = ctx.security_summary.setdefault("tool_usage", {})
        calls = tool_usage.setdefault("tool_calls", [])
        calls.append({
            "name": tool_name,
            "scope": "root",
            "parent_tool": None,
        })


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
) -> Any:
    if isinstance(content, str):
        return sanitizer.sanitize_text(content, ctx)
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                new_text = sanitizer.sanitize_text(part.get("text", ""), ctx)
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
                tool_name,
            )
            items.append(sanitized_value)
            if summary:
                tool_summary = summary
        return items, tool_summary
    if isinstance(tool_output, str):
        if not tool_output:
            return tool_output, None
        entities = _drop_placeholder_entities(
            _coerce_entities(
                sanitizer.pii.normalize(sanitizer.pii.detect(tool_output))
            ),
            tool_output,
        )
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


def _coerce_entities(entities: Iterable[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ent in entities:
        if isinstance(ent, dict):
            out.append(ent)
            continue
        if hasattr(ent, "model_dump"):
            dumped = ent.model_dump()
            if isinstance(dumped, dict):
                out.append(dumped)
    return out


def _extract_agentic_metadata_from_output(
    tool_output: Any,
) -> tuple[Any, Dict[str, str], Dict[str, Any] | None]:
    updates: Dict[str, str] = {}
    child_security_summary = ensure_security_summary()
    has_child_summary = False

    def walk(value: Any) -> Any:
        nonlocal has_child_summary
        if isinstance(value, str):
            parsed = _parse_mapping_like_string(value)
            if isinstance(parsed, (dict, list)):
                return str(walk(parsed))
            return value
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, dict):
            cleaned: Dict[str, Any] = {}
            for key, raw in value.items():
                if key in ("placeholder_map", "placeholderMap"):
                    updates.update(_normalize_placeholder_map(raw))
                    continue
                if key in ("security_summary", "securitySummary") and isinstance(raw, dict):
                    _merge_security_summary(child_security_summary, raw)
                    has_child_summary = True
                    continue
                cleaned[key] = walk(raw)
            return cleaned
        return value

    cleaned_output = walk(tool_output)
    return cleaned_output, updates, (child_security_summary if has_child_summary else None)


def _parse_mapping_like_string(value: str) -> Any:
    text = value.strip()
    if not text or text[0] not in ("{", "["):
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            text = text[brace_start:brace_end + 1]
        else:
            return None

    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
            if isinstance(parsed, (dict, list)):
                return parsed
        except Exception:
            continue
    return None


def _normalize_placeholder_map(raw: Any) -> Dict[str, str]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, list):
        out: Dict[str, str] = {}
        for item in raw:
            if isinstance(item, dict) and "placeholder" in item and "original" in item:
                out[str(item["placeholder"])] = str(item["original"])
        return out
    return {}


def _drop_placeholder_entities(entities: List[Dict[str, Any]], text: str) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for ent in entities:
        snippet = _entity_snippet(ent, text)
        if snippet and PLACEHOLDER_RE.search(snippet):
            continue
        filtered.append(ent)
    return filtered


def _entity_snippet(entity: Dict[str, Any], text: str) -> str | None:
    start = entity.get("start")
    end = entity.get("end")
    try:
        if start is not None and end is not None:
            start_i = int(start)
            end_i = int(end)
            if 0 <= start_i < end_i <= len(text):
                return text[start_i:end_i]
    except Exception:
        pass
    value = entity.get("text")
    if isinstance(value, str) and value:
        return value
    return None


def _merge_security_summary(
    target: Dict[str, Any],
    delta: Dict[str, Any],
    *,
    parent_tool: str | None = None,
) -> None:
    target_sum = ensure_security_summary(target)
    delta_sum = ensure_security_summary(delta)

    target_in = target_sum.get("input_redaction", {})
    delta_in = delta_sum.get("input_redaction", {})
    _merge_entity_summary(
        target_in.setdefault("entities_detected", {"count": 0, "types": {}}),
        delta_in.get("entities_detected") or {"count": 0, "types": {}},
    )
    target_in["sensitive_detected"] = bool(
        target_in.get("sensitive_detected") or delta_in.get("sensitive_detected")
    )

    target_tool = target_sum.get("tool_usage", {})
    delta_tool = delta_sum.get("tool_usage", {})
    target_tool.setdefault("tool_calls", [])
    target_tool["tool_calls"].extend(
        _attach_parent_to_tool_calls(delta_tool.get("tool_calls"), parent_tool=parent_tool)
    )
    target_tool["sensitive_inputs_protected"] = bool(
        target_tool.get("sensitive_inputs_protected") or delta_tool.get("sensitive_inputs_protected")
    )
    target_tool["sensitive_outputs_protected"] = bool(
        target_tool.get("sensitive_outputs_protected") or delta_tool.get("sensitive_outputs_protected")
    )
    target_tool.setdefault("entities_detected_in_tool_outputs", [])
    target_tool["entities_detected_in_tool_outputs"].extend(
        list(delta_tool.get("entities_detected_in_tool_outputs") or [])
    )

    target["input_redaction"] = target_in
    target["tool_usage"] = target_tool


def _attach_parent_to_tool_calls(
    calls: Any,
    *,
    parent_tool: str | None = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
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
