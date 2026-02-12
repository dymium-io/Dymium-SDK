"""SecureRuntime: SDK-owned orchestration loop (placeholder-safe)."""
from __future__ import annotations

from typing import Any, Dict, Iterable

from dymium.core.contracts import AgentRuntime, RuntimeComponents
from dymium.config import RuntimeConfig
from dymium.registry import GLOBAL_REGISTRY, register_default_adapters
from dymium.redaction import RedactionEngine
from dymium.types import ChatRequest
from dymium.adapters.tools import CombinedToolAdapter, LocalToolAdapter


DEFAULT_SYSTEM_PROMPT = (
    "Security notice: sanitized personal data.\n"
    "Some user-provided values are replaced with placeholder tokens such as "
    "PH_EMAIL_ABCDE or PH_NAME_X1Y2Z. Treat each placeholder as the user's real "
    "value and keep it verbatim in all responses. Do not reveal or mention that "
    "sanitization or placeholders exist.\n\n"
    "Rules:\n"
    "- Never attempt to infer, reconstruct, validate, compare, or reason about "
    "the properties of sensitive values represented by placeholders.\n"
    "- Never invent new placeholders. Only use placeholders already present in "
    "the conversation or tool results.\n"
    "- If a task requires the original sensitive value, refuse. Do not ask for "
    "the original value and do not mention any settings or toggles.\n"
    "- If a task requires properties of a placeholdered value (e.g., checking "
    "an email domain, formatting, or validity), refuse and explain that you "
    "cannot access the underlying value.\n"
    "- You may call tools. Tool inputs may include placeholders and will be "
    "resolved by the runtime at execution time. Tool outputs may include "
    "placeholders; keep them as-is.\n"
)


class SecureRuntime(AgentRuntime):
    """Reference orchestration loop (skeleton).

    This class will:
    - run PII detection on new input
    - placeholderize before LLM access
    - resolve placeholders only at tool boundaries
    - re-placeholderize tool outputs
    - stream placeholder updates to the caller
    """

    def __init__(self, components: RuntimeComponents) -> None:
        self.components = components

    @classmethod
    def from_config(cls, config: RuntimeConfig) -> "SecureRuntime":
        config.validate()
        register_default_adapters()
        llm_name, llm_cfg = config.resolve_llm()
        llm = GLOBAL_REGISTRY.create("llm", llm_name, llm_cfg)
        pii = GLOBAL_REGISTRY.create("pii", config.pii, config.pii_config)
        tools = cls._build_tools(config)
        redaction = RedactionEngine()
        components = RuntimeComponents(llm=llm, pii=pii, redaction=redaction, tools=tools)
        return cls(components)

    def run(self, request: ChatRequest | Dict[str, Any]) -> Dict[str, Any]:
        if hasattr(request, "model_dump"):
            request = request.model_dump()
        placeholder_map = self._normalize_placeholder_map(request.get("placeholderMap"))
        input_entities_summary = {"count": 0, "types": {}}
        tool_entities_summary: list[Dict[str, Any]] = []
        tool_calls_history: list[Dict[str, Any]] = []
        tool_inputs_protected = False
        tool_outputs_protected = False

        pii_options = request.get("piiOptions")

        # Build messages from request (canonical input).
        messages_in = request.get("messages")
        if not isinstance(messages_in, list) or not messages_in:
            raise ValueError("ChatRequest.messages is required and must be a non-empty list")

        messages = list(messages_in)
        system_prompt = request.get("system_prompt") or request.get("agent_prompt") or DEFAULT_SYSTEM_PROMPT
        if system_prompt:
            messages = self._ensure_system_message(messages, system_prompt)

        for idx, msg in enumerate(messages):
            if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
                raise ValueError(f"ChatRequest.messages[{idx}] must include 'role' and 'content'")

        messages, placeholder_map = self._sanitize_messages(
            messages,
            placeholder_map,
            input_entities_summary,
            pii_options,
        )

        max_steps = request.get("recursion_limit")
        if max_steps is None:
            max_steps = 3
        tool_results = []
        tool_defs = list(self.components.tools.list_tools() or [])
        for _ in range(max_steps):
            llm_request: Dict[str, Any] = {"messages": messages}
            if tool_defs:
                llm_request["tools"] = tool_defs
            tool_choice = request.get("tool_choice")
            if tool_choice is None:
                tool_choice = request.get("toolChoice")
            if tool_choice is not None:
                llm_request["tool_choice"] = tool_choice

            resp = self.components.llm.chat(llm_request)

            raw_tool_calls = self._extract_raw_tool_calls(resp)
            tool_calls = self._extract_tool_calls(resp)
            if not tool_calls:
                assistant_text = self._extract_assistant_text(resp)
                display_text = self.components.redaction.resolve_placeholders(
                    assistant_text,
                    placeholder_map,
                )
                return {
                    "text": assistant_text,
                    "text_deobfuscated": display_text,
                    "placeholder_map": placeholder_map,
                    "tool_results": tool_results,
                    "messages": messages,
                    "security_summary": self._build_security_summary(
                        input_entities_summary,
                        tool_entities_summary,
                        tool_calls_history,
                        tool_inputs_protected,
                        tool_outputs_protected,
                    ),
                }
            tool_calls_history.extend(tool_calls)

            # Add assistant tool call message before tool responses (OpenAI-compatible).
            messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": self._format_tool_calls_for_messages(raw_tool_calls or tool_calls),
            })

            for tc in tool_calls:
                if self._contains_placeholders(tc.get("arguments"), placeholder_map):
                    tool_inputs_protected = True
                resolved_tc = self._resolve_tool_call(tc, placeholder_map)
                result = self.components.tools.execute(resolved_tc, {"placeholder_map": placeholder_map})
                result, tool_pii = self._placeholderize_tool_output(
                    result,
                    placeholder_map,
                    pii_options,
                    tool_name=resolved_tc.get("name"),
                )
                if tool_pii:
                    tool_entities_summary.append(tool_pii)
                    tool_outputs_protected = True
                tool_results.append({"tool_call": tc, "result": result})
                messages.append({
                    "role": "tool",
                    "tool_call_id": resolved_tc.get("id"),
                    "name": resolved_tc.get("name"),
                    "content": str(result),
                })

        return {
            "text": "",
            "text_deobfuscated": "",
            "placeholder_map": placeholder_map,
            "tool_results": tool_results,
            "messages": messages,
            "error": "recursion_limit_exceeded",
            "security_summary": self._build_security_summary(
                input_entities_summary,
                tool_entities_summary,
                tool_calls_history,
                tool_inputs_protected,
                tool_outputs_protected,
            ),
        }

    def invoke(self, request: ChatRequest | Dict[str, Any]) -> Dict[str, Any]:
        return self.run(request)

    def stream(self, request: ChatRequest | Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        raise NotImplementedError("SecureRuntime.stream is not implemented yet")

    def session(self) -> "SecureSession":
        from dymium.runtime.session import SecureSession
        return SecureSession(runtime=self)

    @staticmethod
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

    @staticmethod
    def _ensure_system_message(messages: list[Dict[str, Any]], system_prompt: str) -> list[Dict[str, Any]]:
        if messages:
            first = messages[0]
            if isinstance(first, dict) and first.get("role") == "system":
                return messages
        return [{"role": "system", "content": system_prompt}, *messages]

    def _sanitize_messages(
        self,
        messages: list[Any],
        placeholder_map: Dict[str, str],
        summary: Dict[str, Any],
        pii_options: Any = None,
    ) -> tuple[list[Any], Dict[str, str]]:
        sanitized: list[Any] = []
        for msg in messages:
            if not isinstance(msg, dict):
                sanitized.append(msg)
                continue
            content = msg.get("content")
            if isinstance(content, str) and content:
                entities = self._coerce_entities(
                    self.components.pii.normalize(self.components.pii.detect(content, options=pii_options))
                )
                if entities:
                    summary["count"] += len(entities)
                    type_counts: Dict[str, int] = summary.get("types", {})
                    for ent in entities:
                        etype = (ent.get("type") or "VALUE") if isinstance(ent, dict) else "VALUE"
                        type_counts[str(etype)] = type_counts.get(str(etype), 0) + 1
                    summary["types"] = type_counts
                    redacted = self.components.redaction.placeholderize(content, entities, placeholder_map)
                    content = redacted.get("text", content)
                    placeholder_map = redacted.get("placeholder_map", placeholder_map)
            new_msg = dict(msg)
            if content is not None:
                new_msg["content"] = content
            sanitized.append(new_msg)
        return sanitized, placeholder_map
        return {}

    @staticmethod
    def _extract_tool_calls(resp: Dict[str, Any]) -> list[Dict[str, Any]]:
        if not isinstance(resp, dict):
            return []
        if isinstance(resp.get("tool_calls"), list):
            return SecureRuntime._normalize_tool_calls(resp["tool_calls"])
        choices = resp.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message", {})
            if isinstance(msg, dict) and isinstance(msg.get("tool_calls"), list):
                return SecureRuntime._normalize_tool_calls(msg["tool_calls"])
        return []

    @staticmethod
    def _extract_raw_tool_calls(resp: Dict[str, Any]) -> list[Dict[str, Any]]:
        if not isinstance(resp, dict):
            return []
        if isinstance(resp.get("tool_calls"), list):
            return resp["tool_calls"]
        choices = resp.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message", {})
            if isinstance(msg, dict) and isinstance(msg.get("tool_calls"), list):
                return msg["tool_calls"]
        return []

    @staticmethod
    def _extract_assistant_text(resp: Dict[str, Any]) -> str:
        if not isinstance(resp, dict):
            return ""
        if isinstance(resp.get("content"), str):
            return resp["content"]
        choices = resp.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message", {})
            if isinstance(msg, dict):
                return msg.get("content", "") or ""
        return ""

    def _resolve_tool_call(self, tool_call: Dict[str, Any], placeholder_map: Dict[str, str]) -> Dict[str, Any]:
        resolved = dict(tool_call)
        args = tool_call.get("arguments")
        if isinstance(args, dict):
            resolved["arguments"] = self._resolve_obj(args, placeholder_map)
        return resolved

    def _resolve_obj(self, obj: Any, placeholder_map: Dict[str, str]) -> Any:
        if isinstance(obj, dict):
            return {k: self._resolve_obj(v, placeholder_map) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._resolve_obj(v, placeholder_map) for v in obj]
        if isinstance(obj, str):
            return self.components.redaction.resolve_placeholders(obj, placeholder_map)
        return obj

    @classmethod
    def _build_tools(cls, config: RuntimeConfig) -> Any:
        local_tools = config.tools if isinstance(config.tools, list) else None
        mcp_adapter = cls._build_mcp_adapter(config.mcp)

        if local_tools is not None:
            local_adapter = LocalToolAdapter(local_tools)
            if mcp_adapter is not None:
                return CombinedToolAdapter([
                    ("local", local_adapter),
                    ("mcp", mcp_adapter),
                ])
            return local_adapter

        if mcp_adapter is not None:
            return mcp_adapter

        if not config.tools:
            raise ValueError("RuntimeConfig.tools is required unless mcp is provided")
        return GLOBAL_REGISTRY.create("tools", config.tools, config.tools_config)

    @staticmethod
    def _build_mcp_adapter(mcp_config: Any) -> Any | None:
        if mcp_config is None:
            return None
        if isinstance(mcp_config, dict):
            if "servers" in mcp_config:
                merged = dict(mcp_config)
                servers = merged.pop("servers")
                return GLOBAL_REGISTRY.create("tools", "mcp_multi", {"servers": servers, **merged})
            return GLOBAL_REGISTRY.create("tools", "mcp", dict(mcp_config))
        if isinstance(mcp_config, list):
            return GLOBAL_REGISTRY.create("tools", "mcp_multi", {"servers": list(mcp_config)})
        raise ValueError("RuntimeConfig.mcp must be a dict or list of dicts")

    def _placeholderize_tool_output(
        self,
        tool_output: Any,
        placeholder_map: Dict[str, str],
        pii_options: Any = None,
        tool_name: str | None = None,
    ) -> Any:
        if isinstance(tool_output, dict):
            # If this looks like a tool-result envelope, only scan the result payload.
            if "result" in tool_output and any(k in tool_output for k in ("id", "name", "isError")):
                sanitized = dict(tool_output)
                sanitized["result"], tool_pii = self._placeholderize_tool_output(
                    tool_output.get("result"),
                    placeholder_map,
                    pii_options,
                    tool_name=tool_name or tool_output.get("name"),
                )
                return sanitized, tool_pii
            sanitized: Dict[str, Any] = {}
            tool_pii = None
            for k, v in tool_output.items():
                sanitized_value, pii_info = self._placeholderize_tool_output(
                    v,
                    placeholder_map,
                    pii_options,
                    tool_name=tool_name,
                )
                sanitized[k] = sanitized_value
                if pii_info:
                    tool_pii = pii_info
            return sanitized, tool_pii
        if isinstance(tool_output, list):
            sanitized_list = []
            tool_pii = None
            for v in tool_output:
                sanitized_value, pii_info = self._placeholderize_tool_output(
                    v,
                    placeholder_map,
                    pii_options,
                    tool_name=tool_name,
                )
                sanitized_list.append(sanitized_value)
                if pii_info:
                    tool_pii = pii_info
            return sanitized_list, tool_pii
        if isinstance(tool_output, str):
            if not tool_output:
                return tool_output, None
            entities = self._coerce_entities(
                self.components.pii.normalize(self.components.pii.detect(tool_output, options=pii_options))
            )
            if not entities:
                return tool_output, None
            redacted = self.components.redaction.placeholderize(tool_output, entities, placeholder_map)
            new_map = redacted.get("placeholder_map", placeholder_map)
            if new_map is not placeholder_map:
                placeholder_map.clear()
                placeholder_map.update(new_map)
            type_counts: Dict[str, int] = {}
            for ent in entities:
                etype = (ent.get("type") or "VALUE") if isinstance(ent, dict) else "VALUE"
                type_counts[str(etype)] = type_counts.get(str(etype), 0) + 1
            return redacted.get("text", tool_output), {
                "tool": tool_name or "unknown",
                "count": len(entities),
                "types": type_counts,
            }
        return tool_output, None

    @staticmethod
    def _build_security_summary(
        input_entities: Dict[str, Any],
        tool_entities: list[Dict[str, Any]],
        tool_calls: list[Dict[str, Any]],
        tool_inputs_protected: bool,
        tool_outputs_protected: bool,
    ) -> Dict[str, Any]:
        tool_names = [tc.get("name") for tc in tool_calls if isinstance(tc, dict)]
        return {
            "input_redaction": {
                "entities_detected": input_entities,
                "sensitive_detected": bool(input_entities.get("count")),
            },
            "tool_usage": {
                "tools_called": tool_names,
                "tool_calls_count": len(tool_names),
                "sensitive_inputs_protected": tool_inputs_protected,
                "sensitive_outputs_protected": tool_outputs_protected,
                "entities_detected_in_tool_outputs": tool_entities,
            },
        }

    @staticmethod
    def _contains_placeholders(obj: Any, placeholder_map: Dict[str, str]) -> bool:
        if not placeholder_map:
            return False
        if isinstance(obj, dict):
            return any(SecureRuntime._contains_placeholders(v, placeholder_map) for v in obj.values())
        if isinstance(obj, list):
            return any(SecureRuntime._contains_placeholders(v, placeholder_map) for v in obj)
        if isinstance(obj, str):
            return any(ph in obj for ph in placeholder_map.keys())
        return False

    @staticmethod
    def _coerce_entities(entities: Iterable[Any]) -> list[Dict[str, Any]]:
        out: list[Dict[str, Any]] = []
        for ent in entities:
            if isinstance(ent, dict):
                out.append(ent)
                continue
            if hasattr(ent, "model_dump"):
                dumped = ent.model_dump()
                if isinstance(dumped, dict):
                    out.append(dumped)
        return out

    @staticmethod
    def _normalize_tool_calls(tool_calls: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
        out: list[Dict[str, Any]] = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            if "name" in tc and "arguments" in tc:
                out.append(tc)
                continue
            fn = tc.get("function") or {}
            name = fn.get("name") or tc.get("name")
            args = fn.get("arguments") if isinstance(fn, dict) else tc.get("arguments")
            if isinstance(args, str):
                try:
                    import json
                    args = json.loads(args)
                except Exception:
                    args = {"_raw": args}
            if not isinstance(args, dict):
                args = {}
            out.append({
                "id": tc.get("id") or tc.get("tool_call_id") or "",
                "name": name,
                "arguments": args,
            })
        return out

    @staticmethod
    def _format_tool_calls_for_messages(tool_calls: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
        out: list[Dict[str, Any]] = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            if "type" in tc and "function" in tc:
                fn = dict(tc["function"]) if isinstance(tc["function"], dict) else {}
                args = fn.get("arguments")
                if isinstance(args, dict):
                    import json
                    fn["arguments"] = json.dumps(args)
                out.append({
                    "id": tc.get("id"),
                    "type": tc.get("type", "function"),
                    "function": fn,
                })
                continue
            name = tc.get("name")
            args = tc.get("arguments") if isinstance(tc.get("arguments"), dict) else {}
            import json
            out.append({
                "id": tc.get("id"),
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args),
                },
            })
        return out
