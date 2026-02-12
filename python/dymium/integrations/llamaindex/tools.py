"""LlamaIndex tool wrapper helpers."""
from __future__ import annotations

from functools import wraps
import inspect
from typing import Any, Callable, Iterable, List

from dymium.sanitization import Sanitizer, SanitizationContext


def wrap_tool_callable(
    func: Callable[..., Any],
    sanitizer: Sanitizer,
    ctx: SanitizationContext,
    *,
    tool_name: str | None = None,
) -> Callable[..., Any]:
    name = tool_name or getattr(func, "__name__", "tool")

    @wraps(func)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        sanitizer.record_tool_call(name, ctx)
        resolved_args, resolved_kwargs = _resolve_invocation(func, args, kwargs, sanitizer, ctx)
        result = func(*resolved_args, **resolved_kwargs)
        return sanitizer.sanitize_tool_output(result, ctx, tool_name=name)

    # Preserve explicit tool identity for frameworks that infer metadata from callables.
    wrapped.__name__ = name
    return wrapped


def wrap_tool(
    tool: Any,
    sanitizer: Sanitizer,
    ctx: SanitizationContext,
    *,
    tool_name: str | None = None,
) -> Any:
    if callable(tool) and not _has_tool_metadata(tool):
        return wrap_tool_callable(
            tool,
            sanitizer,
            ctx,
            tool_name=tool_name,
        )
    return _ToolProxy(
        tool,
        sanitizer,
        ctx,
        tool_name=tool_name,
    )


def wrap_tools(
    tools: Iterable[Any],
    sanitizer: Sanitizer,
    ctx: SanitizationContext,
) -> List[Any]:
    return [
        wrap_tool(tool, sanitizer, ctx)
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
    ) -> None:
        self._tool = tool
        self._sanitizer = sanitizer
        self._ctx = ctx
        metadata = getattr(tool, "metadata", None)
        self._name = tool_name or getattr(metadata, "name", None) or getattr(tool, "__name__", "tool")

    @property
    def metadata(self) -> Any:
        return getattr(self._tool, "metadata", None)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self._sanitizer.record_tool_call(self._name, self._ctx)
        resolved_args, resolved_kwargs = _resolve_invocation(self._tool, args, kwargs, self._sanitizer, self._ctx)
        result = self._tool(*resolved_args, **resolved_kwargs)
        return self._sanitizer.sanitize_tool_output(result, self._ctx, tool_name=self._name)

    async def acall(self, *args: Any, **kwargs: Any) -> Any:
        self._sanitizer.record_tool_call(self._name, self._ctx)
        resolved_args, resolved_kwargs = _resolve_invocation(self._tool, args, kwargs, self._sanitizer, self._ctx)
        if hasattr(self._tool, "acall"):
            result = await self._tool.acall(*resolved_args, **resolved_kwargs)
        else:
            result = self._tool(*resolved_args, **resolved_kwargs)
        return self._sanitizer.sanitize_tool_output(result, self._ctx, tool_name=self._name)


def _resolve_invocation(
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    sanitizer: Sanitizer,
    ctx: SanitizationContext,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    try:
        signature = inspect.signature(func)
        bound = signature.bind_partial(*args, **kwargs)
        resolved = sanitizer.resolve_for_tool(dict(bound.arguments), ctx)
        bound.arguments.clear()
        bound.arguments.update(resolved)
        return bound.args, bound.kwargs
    except Exception:
        # Fallback when signature introspection is unavailable.
        resolved_args = tuple(sanitizer.resolve_for_tool(list(args), ctx))
        resolved_kwargs = sanitizer.resolve_for_tool(dict(kwargs), ctx)
        return resolved_args, resolved_kwargs


def _has_tool_metadata(tool: Any) -> bool:
    return hasattr(tool, "metadata")
