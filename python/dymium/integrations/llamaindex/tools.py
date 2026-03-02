"""LlamaIndex tool wrapper helpers."""
from __future__ import annotations

from functools import wraps
import inspect
from typing import Any, Callable, Dict, Iterable, List

from dymium.sanitization import Sanitizer, SanitizationContext, ensure_security_summary
from dymium.tools import TOOL_TYPE_DELEGATED, normalize_direct_input_mode, normalize_tool_type


def wrap_tool_callable(
    func: Callable[..., Any],
    sanitizer: Sanitizer,
    ctx: SanitizationContext,
    *,
    tool_name: str | None = None,
    tool_type: str | None = None,
    direct_input_mode: str | None = None,
) -> Callable[..., Any]:
    name = tool_name or getattr(func, "__name__", "tool")
    normalized_tool_type = normalize_tool_type(tool_type)
    normalized_direct_input_mode = normalize_direct_input_mode(direct_input_mode)

    @wraps(func)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        sanitizer.record_tool_call(name, ctx)
        resolved_args, resolved_kwargs, agentic_ctx = _resolve_invocation(
            func,
            args,
            kwargs,
            sanitizer,
            ctx,
            tool_type=normalized_tool_type,
            direct_input_mode=normalized_direct_input_mode,
        )
        result = func(*resolved_args, **resolved_kwargs)
        if (
            normalized_tool_type == TOOL_TYPE_DELEGATED
            and isinstance(agentic_ctx, dict)
            and not _has_agentic_metadata(result)
        ):
            _merge_agentic_context(ctx, agentic_ctx, parent_tool=name)
        return sanitizer.sanitize_tool_output(
            result,
            ctx,
            tool_name=name,
            tool_type=normalized_tool_type,
        )

    # Preserve explicit tool identity for frameworks that infer metadata from callables.
    wrapped.__name__ = name
    return wrapped


def wrap_tool(
    tool: Any,
    sanitizer: Sanitizer,
    ctx: SanitizationContext,
    *,
    tool_name: str | None = None,
    tool_type: str | None = None,
    direct_input_mode: str | None = None,
) -> Any:
    if callable(tool) and not _has_tool_metadata(tool):
        return wrap_tool_callable(
            tool,
            sanitizer,
            ctx,
            tool_name=tool_name,
            tool_type=tool_type,
            direct_input_mode=direct_input_mode,
        )
    return _ToolProxy(
        tool,
        sanitizer,
        ctx,
        tool_name=tool_name,
        tool_type=tool_type,
        direct_input_mode=direct_input_mode,
    )


def wrap_tools(
    tools: Iterable[Any],
    sanitizer: Sanitizer,
    ctx: SanitizationContext,
    *,
    tool_types: Dict[str, str] | None = None,
    tool_direct_input_modes: Dict[str, str] | None = None,
) -> List[Any]:
    normalized_tool_types = {
        str(k): normalize_tool_type(v)
        for k, v in (tool_types or {}).items()
    }
    normalized_direct_input_modes = {
        str(k): normalize_direct_input_mode(v)
        for k, v in (tool_direct_input_modes or {}).items()
    }
    return [
        wrap_tool(
            tool,
            sanitizer,
            ctx,
            tool_type=normalized_tool_types.get(_tool_name(tool)),
            direct_input_mode=normalized_direct_input_modes.get(_tool_name(tool)),
        )
        for tool in tools
    ]


class _ToolProxy:
    def __init__(
        self,
        tool: Any,
        sanitizer: Sanitizer,
        ctx: SanitizationContext,
        *,
        tool_name: str | None = None,
        tool_type: str | None = None,
        direct_input_mode: str | None = None,
    ) -> None:
        self._tool = tool
        self._sanitizer = sanitizer
        self._ctx = ctx
        metadata = getattr(tool, "metadata", None)
        self._name = tool_name or getattr(metadata, "name", None) or getattr(tool, "__name__", "tool")
        self._tool_type = normalize_tool_type(tool_type)
        self._direct_input_mode = normalize_direct_input_mode(direct_input_mode)

    @property
    def metadata(self) -> Any:
        return getattr(self._tool, "metadata", None)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self._sanitizer.record_tool_call(self._name, self._ctx)
        resolved_args, resolved_kwargs, agentic_ctx = _resolve_invocation(
            self._tool,
            args,
            kwargs,
            self._sanitizer,
            self._ctx,
            tool_type=self._tool_type,
            direct_input_mode=self._direct_input_mode,
        )
        result = self._tool(*resolved_args, **resolved_kwargs)
        if (
            self._tool_type == TOOL_TYPE_DELEGATED
            and isinstance(agentic_ctx, dict)
            and not _has_agentic_metadata(result)
        ):
            _merge_agentic_context(self._ctx, agentic_ctx, parent_tool=self._name)
        return self._sanitizer.sanitize_tool_output(
            result,
            self._ctx,
            tool_name=self._name,
            tool_type=self._tool_type,
        )

    async def acall(self, *args: Any, **kwargs: Any) -> Any:
        self._sanitizer.record_tool_call(self._name, self._ctx)
        resolved_args, resolved_kwargs, agentic_ctx = _resolve_invocation(
            self._tool,
            args,
            kwargs,
            self._sanitizer,
            self._ctx,
            tool_type=self._tool_type,
            direct_input_mode=self._direct_input_mode,
        )
        if hasattr(self._tool, "acall"):
            result = await self._tool.acall(*resolved_args, **resolved_kwargs)
        else:
            result = self._tool(*resolved_args, **resolved_kwargs)
        if (
            self._tool_type == TOOL_TYPE_DELEGATED
            and isinstance(agentic_ctx, dict)
            and not _has_agentic_metadata(result)
        ):
            _merge_agentic_context(self._ctx, agentic_ctx, parent_tool=self._name)
        return self._sanitizer.sanitize_tool_output(
            result,
            self._ctx,
            tool_name=self._name,
            tool_type=self._tool_type,
        )


def _resolve_invocation(
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    sanitizer: Sanitizer,
    ctx: SanitizationContext,
    *,
    tool_type: str | None = None,
    direct_input_mode: str | None = None,
) -> tuple[tuple[Any, ...], dict[str, Any], Dict[str, Any] | None]:
    normalized_tool_type = normalize_tool_type(tool_type)
    normalized_direct_input_mode = normalize_direct_input_mode(direct_input_mode)
    agentic_ctx: Dict[str, Any] | None = None
    try:
        signature = inspect.signature(func)
        bound = signature.bind_partial(*args, **kwargs)
        resolved = sanitizer.resolve_for_tool(
            dict(bound.arguments),
            ctx,
            tool_type=normalized_tool_type,
            direct_input_mode=normalized_direct_input_mode,
        )
        if normalized_tool_type == TOOL_TYPE_DELEGATED and isinstance(resolved, dict):
            agentic_ctx = _build_agentic_context(resolved.get("dymium_context"), ctx)
            if _accepts_named_arg(func, "dymium_context"):
                resolved = dict(resolved)
                resolved["dymium_context"] = agentic_ctx
        bound.arguments.clear()
        bound.arguments.update(resolved)
        return bound.args, bound.kwargs, agentic_ctx
    except Exception:
        # Fallback when signature introspection is unavailable.
        resolved_args = tuple(
            sanitizer.resolve_for_tool(
                list(args),
                ctx,
                tool_type=normalized_tool_type,
                direct_input_mode=normalized_direct_input_mode,
            )
        )
        resolved_kwargs = sanitizer.resolve_for_tool(
            dict(kwargs),
            ctx,
            tool_type=normalized_tool_type,
            direct_input_mode=normalized_direct_input_mode,
        )
        if normalized_tool_type == TOOL_TYPE_DELEGATED and _accepts_named_arg(func, "dymium_context"):
            agentic_ctx = _build_agentic_context(resolved_kwargs.get("dymium_context"), ctx)
            resolved_kwargs = dict(resolved_kwargs)
            resolved_kwargs["dymium_context"] = agentic_ctx
        return resolved_args, resolved_kwargs, agentic_ctx


def _has_tool_metadata(tool: Any) -> bool:
    return hasattr(tool, "metadata")


def _accepts_named_arg(func: Callable[..., Any], name: str) -> bool:
    try:
        params = inspect.signature(func).parameters.values()
    except Exception:
        return False
    for param in params:
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if param.name == name and param.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return True
    return False


def _build_agentic_context(existing: Any, ctx: SanitizationContext) -> Dict[str, Any]:
    out = dict(existing) if isinstance(existing, dict) else {}
    if "placeholder_map" not in out:
        out["placeholder_map"] = dict(ctx.placeholder_map)
    if "security_summary" not in out:
        out["security_summary"] = ensure_security_summary()
    return out


def _normalize_placeholder_map(raw: Any) -> Dict[str, str]:
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, list):
        out: Dict[str, str] = {}
        for item in raw:
            if isinstance(item, dict) and "placeholder" in item and "original" in item:
                out[str(item["placeholder"])] = str(item["original"])
        return out
    return {}


def _merge_type_counts(left: Dict[str, int] | None, right: Dict[str, int] | None) -> Dict[str, int]:
    out: Dict[str, int] = dict(left or {})
    for k, v in (right or {}).items():
        out[str(k)] = out.get(str(k), 0) + int(v)
    return out


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


def _merge_agentic_context(
    ctx: SanitizationContext,
    agentic_ctx: Dict[str, Any],
    *,
    parent_tool: str | None = None,
) -> None:
    child_map = _normalize_placeholder_map(agentic_ctx.get("placeholder_map"))
    if child_map:
        ctx.placeholder_map.update(child_map)
    child_summary = agentic_ctx.get("security_summary")
    if isinstance(child_summary, dict):
        ctx.security_summary = _merge_security_summaries(
            ctx.security_summary,
            child_summary,
            parent_tool=parent_tool,
        )


def _has_agentic_metadata(value: Any) -> bool:
    if isinstance(value, dict):
        if any(k in value for k in ("placeholder_map", "placeholderMap", "security_summary", "securitySummary")):
            return True
        return any(_has_agentic_metadata(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_agentic_metadata(item) for item in value)
    return False


def _tool_name(tool: Any) -> str | None:
    metadata = getattr(tool, "metadata", None)
    return getattr(metadata, "name", None) or getattr(tool, "name", None) or getattr(tool, "__name__", None)
