"""High-level LlamaIndex agent workflow helper."""
from __future__ import annotations

import inspect
from typing import Any, Iterable

from dymium.runtime.secure_runtime import DEFAULT_SYSTEM_PROMPT
from dymium.sanitization import Sanitizer, SanitizationContext, ensure_security_summary

from .llm import SanitizedLLM
from .tools import wrap_tools


class SanitizedAgentWorkflow:
    """Proxy that sanitizes run inputs before dispatching to AgentWorkflow."""

    def __init__(
        self,
        workflow: Any,
        sanitizer: Sanitizer,
        ctx: SanitizationContext,
    ) -> None:
        self._workflow = workflow
        self._sanitizer = sanitizer
        self._ctx = ctx

    def run(
        self,
        user_msg: Any = None,
        chat_history: list[Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        safe_user_msg = self._sanitize_message(user_msg)
        safe_history = None
        if chat_history is not None:
            safe_history = self._sanitizer.sanitize_messages(chat_history, self._ctx)
        result = self._workflow.run(user_msg=safe_user_msg, chat_history=safe_history, **kwargs)
        if inspect.isawaitable(result):
            async def _await_result() -> Any:
                resolved = await result
                return self._deobfuscate_result(resolved)
            return _await_result()
        return self._deobfuscate_result(result)

    def _sanitize_message(self, msg: Any) -> Any:
        if msg is None:
            return None
        if isinstance(msg, str):
            return self._sanitizer.sanitize_text(msg, self._ctx)
        out = self._sanitizer.sanitize_messages([msg], self._ctx)
        return out[0] if out else msg

    def _deobfuscate_result(self, result: Any) -> Any:
        if isinstance(result, str):
            return self._sanitizer.deobfuscate(result, self._ctx)
        if isinstance(result, dict):
            return _deobfuscate_dict(result, self._sanitizer, self._ctx)

        response = getattr(result, "response", None)
        if isinstance(response, str):
            try:
                setattr(result, "response", self._sanitizer.deobfuscate(response, self._ctx))
            except Exception:
                pass
        elif hasattr(response, "content") and isinstance(getattr(response, "content", None), str):
            try:
                response.content = self._sanitizer.deobfuscate(response.content, self._ctx)
            except Exception:
                pass

        if hasattr(result, "content") and isinstance(getattr(result, "content", None), str):
            try:
                result.content = self._sanitizer.deobfuscate(result.content, self._ctx)
            except Exception:
                pass
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._workflow, name)


def create_sanitized_agent_workflow(
    tools_or_functions: Iterable[Any],
    llm: Any,
    sanitizer: Sanitizer,
    *,
    ctx: SanitizationContext | None = None,
    system_prompt: str | None = DEFAULT_SYSTEM_PROMPT,
    tool_types: dict[str, str] | None = None,
    **kwargs: Any,
) -> Any:
    try:
        from llama_index.core.agent.workflow import AgentWorkflow
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "LlamaIndex is not installed. Install with: pip install llama-index llama-index-llms-openai"
        ) from exc

    ctx = ctx or SanitizationContext(security_summary=ensure_security_summary())
    safe_llm = SanitizedLLM(
        llm,
        sanitizer,
        ctx=ctx,
        system_prompt=system_prompt,
    )
    safe_tools = wrap_tools(
        tools_or_functions,
        sanitizer,
        ctx,
        tool_types=tool_types,
    )

    workflow = AgentWorkflow.from_tools_or_functions(
        list(safe_tools),
        llm=safe_llm,
        system_prompt=None,
        **kwargs,
    )
    return SanitizedAgentWorkflow(workflow, sanitizer, ctx)


def _deobfuscate_dict(
    value: dict[str, Any],
    sanitizer: Sanitizer,
    ctx: SanitizationContext,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in value.items():
        if isinstance(v, str):
            out[k] = sanitizer.deobfuscate(v, ctx)
        elif isinstance(v, dict):
            out[k] = _deobfuscate_dict(v, sanitizer, ctx)
        elif isinstance(v, list):
            out[k] = _deobfuscate_list(v, sanitizer, ctx)
        else:
            out[k] = v
    return out


def _deobfuscate_list(
    value: list[Any],
    sanitizer: Sanitizer,
    ctx: SanitizationContext,
) -> list[Any]:
    out: list[Any] = []
    for item in value:
        if isinstance(item, str):
            out.append(sanitizer.deobfuscate(item, ctx))
        elif isinstance(item, dict):
            out.append(_deobfuscate_dict(item, sanitizer, ctx))
        elif isinstance(item, list):
            out.append(_deobfuscate_list(item, sanitizer, ctx))
        else:
            out.append(item)
    return out
