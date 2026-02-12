"""LlamaIndex tool wrapper helpers."""
from __future__ import annotations

from typing import Any, Callable

from dymium.sanitization import Sanitizer, SanitizationContext


def wrap_tool_callable(
    func: Callable[..., Any],
    sanitizer: Sanitizer,
    ctx: SanitizationContext,
    *,
    tool_name: str | None = None,
    pii_options: dict[str, Any] | None = None,
) -> Callable[..., Any]:
    name = tool_name or getattr(func, "__name__", "tool")

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        resolved_kwargs = sanitizer.resolve_for_tool(kwargs, ctx)
        result = func(*args, **resolved_kwargs)
        return sanitizer.sanitize_tool_output(result, ctx, pii_options, name)

    return wrapped
