"""High-level LlamaIndex agent workflow helper."""
from __future__ import annotations

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
        pii_options: dict[str, Any] | None = None,
    ) -> None:
        self._workflow = workflow
        self._sanitizer = sanitizer
        self._ctx = ctx
        self._pii_options = pii_options

    def run(
        self,
        user_msg: Any = None,
        chat_history: list[Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        safe_user_msg = self._sanitize_message(user_msg)
        safe_history = None
        if chat_history is not None:
            safe_history = self._sanitizer.sanitize_messages(chat_history, self._ctx, self._pii_options)
        return self._workflow.run(user_msg=safe_user_msg, chat_history=safe_history, **kwargs)

    def _sanitize_message(self, msg: Any) -> Any:
        if msg is None:
            return None
        if isinstance(msg, str):
            return self._sanitizer.sanitize_text(msg, self._ctx, self._pii_options)
        out = self._sanitizer.sanitize_messages([msg], self._ctx, self._pii_options)
        return out[0] if out else msg

    def __getattr__(self, name: str) -> Any:
        return getattr(self._workflow, name)


def create_sanitized_agent_workflow(
    tools_or_functions: Iterable[Any],
    llm: Any,
    sanitizer: Sanitizer,
    *,
    ctx: SanitizationContext | None = None,
    system_prompt: str | None = DEFAULT_SYSTEM_PROMPT,
    pii_options: dict[str, Any] | None = None,
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
        pii_options=pii_options,
    )
    safe_tools = wrap_tools(tools_or_functions, sanitizer, ctx, pii_options=pii_options)

    workflow = AgentWorkflow.from_tools_or_functions(
        list(safe_tools),
        llm=safe_llm,
        system_prompt=None,
        **kwargs,
    )
    return SanitizedAgentWorkflow(workflow, sanitizer, ctx, pii_options=pii_options)
