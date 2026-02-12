"""LlamaIndex LLM wrapper."""
from __future__ import annotations

from typing import Any, Iterable, List

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
                text = _extract_response_text(resp)
            else:
                raise RuntimeError("Wrapped LLM does not support complete or chat")

            # Keep model output placeholder-safe
            safe_text = self._sanitizer.sanitize_text(text, self._ctx, self._pii_options)
            return CompletionResponse(text=safe_text, raw=resp)

        def stream_complete(self, prompt: str, **kwargs: Any) -> Iterable[CompletionResponse]:
            yield self.complete(prompt, **kwargs)

        def chat(self, messages: List[Any], **kwargs: Any) -> Any:
            if not hasattr(self._llm, "chat"):
                raise RuntimeError("Wrapped LLM does not support chat")
            safe_messages = self._sanitizer.sanitize_messages(messages, self._ctx, self._pii_options)
            resp = self._llm.chat(safe_messages, **kwargs)
            return _sanitize_chat_response(resp, self._sanitizer, self._ctx, self._pii_options)

        async def achat(self, messages: List[Any], **kwargs: Any) -> Any:
            if not hasattr(self._llm, "achat"):
                raise RuntimeError("Wrapped LLM does not support achat")
            safe_messages = self._sanitizer.sanitize_messages(messages, self._ctx, self._pii_options)
            resp = await self._llm.achat(safe_messages, **kwargs)
            return _sanitize_chat_response(resp, self._sanitizer, self._ctx, self._pii_options)

        def stream_chat(self, messages: List[Any], **kwargs: Any) -> Iterable[Any]:
            if not hasattr(self._llm, "stream_chat"):
                raise RuntimeError("Wrapped LLM does not support stream_chat")
            safe_messages = self._sanitizer.sanitize_messages(messages, self._ctx, self._pii_options)
            for chunk in self._llm.stream_chat(safe_messages, **kwargs):
                yield _sanitize_chat_response(chunk, self._sanitizer, self._ctx, self._pii_options)

        async def astream_chat(self, messages: List[Any], **kwargs: Any) -> Any:
            if not hasattr(self._llm, "astream_chat"):
                raise RuntimeError("Wrapped LLM does not support astream_chat")
            safe_messages = self._sanitizer.sanitize_messages(messages, self._ctx, self._pii_options)
            async for chunk in self._llm.astream_chat(safe_messages, **kwargs):
                yield _sanitize_chat_response(chunk, self._sanitizer, self._ctx, self._pii_options)

        def chat_with_tools(self, tools: List[Any], user_msg: Any = None, chat_history: List[Any] | None = None, **kwargs: Any) -> Any:
            if not hasattr(self._llm, "chat_with_tools"):
                raise RuntimeError("Wrapped LLM does not support chat_with_tools")
            safe_user_msg = _sanitize_optional_message(user_msg, self._sanitizer, self._ctx, self._pii_options)
            safe_history = self._sanitizer.sanitize_messages(chat_history or [], self._ctx, self._pii_options)
            resp = self._llm.chat_with_tools(
                tools,
                user_msg=safe_user_msg,
                chat_history=safe_history,
                **kwargs,
            )
            return _sanitize_chat_response(resp, self._sanitizer, self._ctx, self._pii_options)

        async def achat_with_tools(
            self,
            tools: List[Any],
            user_msg: Any = None,
            chat_history: List[Any] | None = None,
            **kwargs: Any,
        ) -> Any:
            if not hasattr(self._llm, "achat_with_tools"):
                raise RuntimeError("Wrapped LLM does not support achat_with_tools")
            safe_user_msg = _sanitize_optional_message(user_msg, self._sanitizer, self._ctx, self._pii_options)
            safe_history = self._sanitizer.sanitize_messages(chat_history or [], self._ctx, self._pii_options)
            resp = await self._llm.achat_with_tools(
                tools,
                user_msg=safe_user_msg,
                chat_history=safe_history,
                **kwargs,
            )
            return _sanitize_chat_response(resp, self._sanitizer, self._ctx, self._pii_options)

        def get_tool_calls_from_response(self, response: Any, **kwargs: Any) -> Any:
            if not hasattr(self._llm, "get_tool_calls_from_response"):
                raise RuntimeError("Wrapped LLM does not support get_tool_calls_from_response")
            return self._llm.get_tool_calls_from_response(response, **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._llm, name)



def _prepend_system(system_prompt: str | None, prompt: str) -> str:
    if not system_prompt:
        return prompt
    return f"{system_prompt}\n\n{prompt}"


def _extract_response_text(resp: Any) -> str:
    text = getattr(resp, "text", None)
    if isinstance(text, str):
        return text
    message = getattr(resp, "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if message is not None:
        return str(message)
    return str(resp)


def _sanitize_optional_message(
    msg: Any,
    sanitizer: Sanitizer,
    ctx: SanitizationContext,
    pii_options: dict[str, Any] | None,
) -> Any:
    if msg is None:
        return None
    if isinstance(msg, str):
        return sanitizer.sanitize_text(msg, ctx, pii_options)
    sanitized = sanitizer.sanitize_messages([msg], ctx, pii_options)
    return sanitized[0] if sanitized else msg


def _sanitize_chat_response(
    resp: Any,
    sanitizer: Sanitizer,
    ctx: SanitizationContext,
    pii_options: dict[str, Any] | None,
) -> Any:
    message = getattr(resp, "message", None)
    if message is None:
        return resp
    content = getattr(message, "content", None)
    if not isinstance(content, str):
        return resp
    safe_content = sanitizer.sanitize_text(content, ctx, pii_options)
    try:
        setattr(message, "content", safe_content)
    except Exception:
        pass
    return resp
