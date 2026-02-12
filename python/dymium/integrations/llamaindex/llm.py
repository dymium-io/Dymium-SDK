"""LlamaIndex LLM wrapper."""
from __future__ import annotations

from typing import Any, Iterable

from dymium.runtime.secure_runtime import DEFAULT_SYSTEM_PROMPT
from dymium.sanitization import Sanitizer, SanitizationContext, ensure_security_summary


try:  # Optional dependency
    from llama_index.core.llms import CustomLLM, CompletionResponse, LLMMetadata, ChatMessage
except Exception:  # pragma: no cover
    CustomLLM = None  # type: ignore
    CompletionResponse = None  # type: ignore
    LLMMetadata = None  # type: ignore
    ChatMessage = None  # type: ignore


class SanitizedLLM:  # fallback wrapper if LlamaIndex isn't installed
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("LlamaIndex is not installed. Install with: pip install llama-index")


if CustomLLM is not None:

    class SanitizedLLM(CustomLLM):
        """LlamaIndex LLM wrapper that enforces placeholder boundaries."""

        def __init__(
            self,
            llm: Any,
            sanitizer: Sanitizer,
            *,
            ctx: SanitizationContext | None = None,
            system_prompt: str | None = DEFAULT_SYSTEM_PROMPT,
            pii_options: dict[str, Any] | None = None,
        ) -> None:
            self._llm = llm
            self._sanitizer = sanitizer
            self._ctx = ctx or SanitizationContext(security_summary=ensure_security_summary())
            self._system_prompt = system_prompt
            self._pii_options = pii_options

        @property
        def metadata(self) -> LLMMetadata:
            meta = getattr(self._llm, "metadata", None)
            if meta is not None:
                return meta
            return LLMMetadata(model_name="dymium-sanitized", context_window=4096, num_output=512)

        def complete(self, prompt: str, **kwargs: Any) -> CompletionResponse:
            full_prompt = _prepend_system(self._system_prompt, prompt)
            sanitized_prompt = self._sanitizer.sanitize_text(full_prompt, self._ctx, self._pii_options)

            if hasattr(self._llm, "complete"):
                resp = self._llm.complete(sanitized_prompt, **kwargs)
                text = getattr(resp, "text", None) or str(resp)
            elif hasattr(self._llm, "chat") and ChatMessage is not None:
                messages = [ChatMessage(role="user", content=sanitized_prompt)]
                resp = self._llm.chat(messages, **kwargs)
                text = getattr(resp, "message", None) or getattr(resp, "text", None) or str(resp)
            else:
                raise RuntimeError("Wrapped LLM does not support complete or chat")

            # Keep model output placeholder-safe
            safe_text = self._sanitizer.sanitize_text(text, self._ctx, self._pii_options)
            return CompletionResponse(text=safe_text, raw=resp)

        def stream_complete(self, prompt: str, **kwargs: Any) -> Iterable[CompletionResponse]:
            yield self.complete(prompt, **kwargs)



def _prepend_system(system_prompt: str | None, prompt: str) -> str:
    if not system_prompt:
        return prompt
    return f"{system_prompt}\n\n{prompt}"
