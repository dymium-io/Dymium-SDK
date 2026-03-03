"""SecureRuntime: SDK-owned orchestration loop (placeholder-safe)."""
from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable

from dymium.core.contracts import AgentRuntime, RuntimeComponents
from dymium.config import RuntimeConfig
from dymium.registry import GLOBAL_REGISTRY, register_default_adapters
from dymium.redaction import RedactionEngine
from dymium.delegation.transport import (
    RUNTIME_CONTEXT_ID_KEY,
    RUNTIME_CONTEXT_MARKER_KEY,
    RUNTIME_CONTEXT_MARKER_VALUE,
)
from dymium.types import ChatRequest
from dymium.adapters.tools import CombinedToolAdapter, LocalToolAdapter
from dymium.tools import (
    TOOL_TYPE_DELEGATED,
    normalize_direct_input_mode,
    normalize_tool_type,
    should_resolve_tool_inputs,
)


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
    "- You may call tools. Tool inputs may include placeholders. Direct "
    "tool inputs may be resolved by the runtime at execution time depending on "
    "tool policy; delegated tools may receive placeholders directly. Tool outputs "
    "may include placeholders; keep them as-is.\n"
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

    def __init__(
        self,
        components: RuntimeComponents,
        *,
        tool_types: Dict[str, str] | None = None,
        tool_direct_input_modes: Dict[str, str] | None = None,
    ) -> None:
        self.components = components
        self._tool_types = {
            str(k): normalize_tool_type(v)
            for k, v in (tool_types or {}).items()
        }
        self._tool_direct_input_modes = {
            str(k): normalize_direct_input_mode(v)
            for k, v in (tool_direct_input_modes or {}).items()
        }

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
        return cls(
            components,
            tool_types=config.tool_types,
            tool_direct_input_modes=config.tool_direct_input_modes,
        )

    def run(self, request: ChatRequest | Dict[str, Any]) -> Dict[str, Any]:
        if hasattr(request, "model_dump"):
            request = request.model_dump()
        placeholder_map = self._normalize_placeholder_map(
            request.get("placeholderMap") or request.get("placeholder_map")
        )
        input_entities_summary = {"count": 0, "types": {}}
        tool_entities_summary: list[Dict[str, Any]] = []
        tool_calls_history: list[Dict[str, Any]] = []
        tool_inputs_protected = False
        tool_outputs_protected = False

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
        )

        max_steps = request.get("recursion_limit")
        if max_steps is None:
            max_steps = 3
        tool_results = []
        tool_defs = list(self.components.tools.list_tools() or [])
        tool_types = self._tool_types_by_name(tool_defs)
        tool_direct_input_modes = self._tool_direct_input_modes_by_name(tool_defs)
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
                    "text": display_text,
                    "placeholder_map": placeholder_map,
                    "tool_results": tool_results,
                    "messages": self._deobfuscate_messages(messages, placeholder_map),
                    "security_summary": self._build_security_summary(
                        input_entities_summary,
                        tool_entities_summary,
                        tool_calls_history,
                        tool_inputs_protected,
                        tool_outputs_protected,
                    ),
                }
            for tc in tool_calls:
                if isinstance(tc, dict) and isinstance(tc.get("name"), str):
                    tool_calls_history.append({
                        "name": tc.get("name"),
                        "parent_tool": None,
                        "scope": "root",
                    })

            # Add assistant tool call message before tool responses (OpenAI-compatible).
            messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": self._format_tool_calls_for_messages(raw_tool_calls or tool_calls),
            })

            for tc in tool_calls:
                tool_type = normalize_tool_type(tool_types.get(tc.get("name")))
                direct_input_mode = normalize_direct_input_mode(
                    tool_direct_input_modes.get(tc.get("name"))
                )
                if self._contains_placeholders(tc.get("arguments"), placeholder_map):
                    tool_inputs_protected = True
                execute_tc = self._prepare_tool_call_for_execution(
                    tc,
                    placeholder_map,
                    tool_type=tool_type,
                    direct_input_mode=direct_input_mode,
                )
                agentic_ctx: Dict[str, Any] | None = None
                if tool_type == TOOL_TYPE_DELEGATED:
                    execute_tc, agentic_ctx = self._attach_agentic_context(
                        execute_tc,
                        placeholder_map,
                    )
                result = self.components.tools.execute(execute_tc, {
                    "placeholder_map": placeholder_map,
                    "dymium_context": agentic_ctx or {},
                })
                if tool_type == TOOL_TYPE_DELEGATED:
                    if isinstance(agentic_ctx, dict):
                        child_map = self._normalize_placeholder_map(agentic_ctx.get("placeholder_map"))
                        if child_map:
                            placeholder_map.update(child_map)
                        child_summary_ctx = agentic_ctx.get("security_summary")
                        if isinstance(child_summary_ctx, dict):
                            tool_inputs_protected, tool_outputs_protected = self._merge_child_security_summary(
                                child_summary_ctx,
                                input_entities_summary,
                                tool_entities_summary,
                                tool_calls_history,
                                parent_tool_name=execute_tc.get("name"),
                                tool_inputs_protected=tool_inputs_protected,
                                tool_outputs_protected=tool_outputs_protected,
                            )
                    result, map_updates, child_security_summary = self._extract_runtime_managed_context_from_tool_result(
                        result,
                        expected_context=agentic_ctx,
                    )
                    if map_updates:
                        placeholder_map.update(map_updates)
                    if child_security_summary:
                        tool_inputs_protected, tool_outputs_protected = self._merge_child_security_summary(
                            child_security_summary,
                            input_entities_summary,
                            tool_entities_summary,
                            tool_calls_history,
                            parent_tool_name=execute_tc.get("name"),
                            tool_inputs_protected=tool_inputs_protected,
                            tool_outputs_protected=tool_outputs_protected,
                        )
                result, tool_pii = self._placeholderize_tool_output(
                    result,
                    placeholder_map,
                    tool_name=execute_tc.get("name"),
                )
                if tool_pii:
                    tool_entities_summary.append(tool_pii)
                    tool_outputs_protected = True
                tool_results.append({"tool_call": tc, "result": result})
                messages.append({
                    "role": "tool",
                    "tool_call_id": execute_tc.get("id"),
                    "name": execute_tc.get("name"),
                    "content": str(result),
                })

        return {
            "text": "",
            "placeholder_map": placeholder_map,
            "tool_results": tool_results,
            "messages": self._deobfuscate_messages(messages, placeholder_map),
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
        return {}

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
    ) -> tuple[list[Any], Dict[str, str]]:
        sanitized: list[Any] = []
        for msg in messages:
            if not isinstance(msg, dict):
                sanitized.append(msg)
                continue
            content = msg.get("content")
            if isinstance(content, str) and content:
                entities = self._coerce_entities(
                    self.components.pii.normalize(self.components.pii.detect(content))
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

    def _deobfuscate_messages(
        self,
        messages: list[Any],
        placeholder_map: Dict[str, str],
    ) -> list[Any]:
        out: list[Any] = []
        for msg in messages:
            if not isinstance(msg, dict):
                out.append(msg)
                continue
            updated = dict(msg)
            content = updated.get("content")
            if isinstance(content, str):
                updated["content"] = self.components.redaction.resolve_placeholders(content, placeholder_map)
            elif isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        part_updated = dict(part)
                        part_updated["text"] = self.components.redaction.resolve_placeholders(
                            part["text"],
                            placeholder_map,
                        )
                        parts.append(part_updated)
                    else:
                        parts.append(part)
                updated["content"] = parts
            out.append(updated)
        return out

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

    def _prepare_tool_call_for_execution(
        self,
        tool_call: Dict[str, Any],
        placeholder_map: Dict[str, str],
        *,
        tool_type: str | None = None,
        direct_input_mode: str | None = None,
    ) -> Dict[str, Any]:
        if not should_resolve_tool_inputs(
            tool_type=normalize_tool_type(tool_type),
            direct_input_mode=normalize_direct_input_mode(direct_input_mode),
        ):
            return dict(tool_call)
        resolved = dict(tool_call)
        args = tool_call.get("arguments")
        if isinstance(args, dict):
            resolved["arguments"] = self._resolve_obj(args, placeholder_map)
        return resolved

    @staticmethod
    def _attach_agentic_context(
        tool_call: Dict[str, Any],
        placeholder_map: Dict[str, str],
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        resolved = dict(tool_call)
        args = resolved.get("arguments")
        if not isinstance(args, dict):
            args = {}
        args = dict(args)
        # Runtime-owned context only: ignore any caller/tool-supplied dymium_context.
        args.pop("dymium_context", None)
        agentic_ctx = {
            "placeholder_map": dict(placeholder_map),
            RUNTIME_CONTEXT_MARKER_KEY: RUNTIME_CONTEXT_MARKER_VALUE,
            RUNTIME_CONTEXT_ID_KEY: uuid.uuid4().hex,
        }
        args["dymium_context"] = agentic_ctx
        resolved["arguments"] = args
        return resolved, agentic_ctx

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

    def _tool_types_by_name(self, tool_defs: Iterable[Dict[str, Any]]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for tool in tool_defs:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name")
            if not name:
                continue
            out[str(name)] = normalize_tool_type(tool.get("tool_type"))
        out.update(self._tool_types)
        return out

    def _tool_direct_input_modes_by_name(self, tool_defs: Iterable[Dict[str, Any]]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for tool in tool_defs:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name")
            if not name:
                continue
            out[str(name)] = normalize_direct_input_mode(
                tool.get("direct_input_mode") or tool.get("directInputMode")
            )
        out.update(self._tool_direct_input_modes)
        return out

    @classmethod
    def _extract_agentic_metadata_from_tool_result(
        cls,
        result: Any,
    ) -> tuple[Any, Dict[str, str], Dict[str, Any] | None]:
        updates: Dict[str, str] = {}
        child_summary: Dict[str, Any] | None = None
        if not isinstance(result, dict):
            return result, updates, child_summary

        updated = dict(result)
        dymium_ctx = updated.pop("dymium_context", None)
        if isinstance(dymium_ctx, dict):
            updates.update(cls._normalize_placeholder_map(dymium_ctx.get("placeholder_map")))
            raw_ctx_summary = dymium_ctx.get("security_summary")
            if isinstance(raw_ctx_summary, dict):
                child_summary = cls._merge_summary_dicts(child_summary, raw_ctx_summary)
        updates.update(cls._normalize_placeholder_map(updated.pop("placeholder_map", None)))
        updates.update(cls._normalize_placeholder_map(updated.pop("placeholderMap", None)))
        raw_child_summary = updated.pop("security_summary", None) or updated.pop("securitySummary", None)
        if isinstance(raw_child_summary, dict):
            child_summary = raw_child_summary

        payload = updated.get("result")
        if isinstance(payload, dict):
            payload_updated = dict(payload)
            updates.update(cls._normalize_placeholder_map(payload_updated.pop("placeholder_map", None)))
            updates.update(cls._normalize_placeholder_map(payload_updated.pop("placeholderMap", None)))
            raw_child_summary = payload_updated.pop("security_summary", None) or payload_updated.pop("securitySummary", None)
            if isinstance(raw_child_summary, dict):
                child_summary = cls._merge_summary_dicts(child_summary, raw_child_summary)
            updated["result"] = payload_updated

        return updated, updates, child_summary

    @classmethod
    def _extract_runtime_managed_context_from_tool_result(
        cls,
        result: Any,
        *,
        expected_context: Dict[str, Any] | None = None,
    ) -> tuple[Any, Dict[str, str], Dict[str, Any] | None]:
        cleaned, _, _ = cls._extract_agentic_metadata_from_tool_result(result)
        updates: Dict[str, str] = {}
        child_summary: Dict[str, Any] | None = None
        expected_context_id = expected_context.get(RUNTIME_CONTEXT_ID_KEY) if isinstance(expected_context, dict) else None
        if not isinstance(expected_context_id, str):
            return cleaned, updates, child_summary

        candidates: list[Any] = []
        if isinstance(result, dict):
            candidates.append(result.get("dymium_context"))
            payload = result.get("result")
            if isinstance(payload, dict):
                candidates.append(payload.get("dymium_context"))
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if candidate.get(RUNTIME_CONTEXT_MARKER_KEY) != RUNTIME_CONTEXT_MARKER_VALUE:
                continue
            if candidate.get(RUNTIME_CONTEXT_ID_KEY) != expected_context_id:
                continue
            updates.update(cls._normalize_placeholder_map(candidate.get("placeholder_map")))
            summary = candidate.get("security_summary")
            if isinstance(summary, dict):
                child_summary = dict(summary)
            break
        return cleaned, updates, child_summary

    @classmethod
    def _merge_summary_dicts(cls, left: Dict[str, Any] | None, right: Dict[str, Any] | None) -> Dict[str, Any]:
        if not isinstance(left, dict):
            return dict(right or {})
        if not isinstance(right, dict):
            return dict(left)

        out: Dict[str, Any] = dict(left)
        left_in = left.get("input_redaction") if isinstance(left.get("input_redaction"), dict) else {}
        right_in = right.get("input_redaction") if isinstance(right.get("input_redaction"), dict) else {}
        out["input_redaction"] = {
            "entities_detected": {
                "count": int((left_in.get("entities_detected") or {}).get("count", 0))
                + int((right_in.get("entities_detected") or {}).get("count", 0)),
                "types": cls._merge_type_counts(
                    (left_in.get("entities_detected") or {}).get("types"),
                    (right_in.get("entities_detected") or {}).get("types"),
                ),
            },
            "sensitive_detected": bool(left_in.get("sensitive_detected") or right_in.get("sensitive_detected")),
        }

        left_tool = left.get("tool_usage") if isinstance(left.get("tool_usage"), dict) else {}
        right_tool = right.get("tool_usage") if isinstance(right.get("tool_usage"), dict) else {}
        out["tool_usage"] = {
            "tool_calls": list(left_tool.get("tool_calls") or []) + list(right_tool.get("tool_calls") or []),
            "sensitive_inputs_protected": bool(
                left_tool.get("sensitive_inputs_protected") or right_tool.get("sensitive_inputs_protected")
            ),
            "sensitive_outputs_protected": bool(
                left_tool.get("sensitive_outputs_protected") or right_tool.get("sensitive_outputs_protected")
            ),
            "entities_detected_in_tool_outputs": list(left_tool.get("entities_detected_in_tool_outputs") or [])
            + list(right_tool.get("entities_detected_in_tool_outputs") or []),
        }
        return out

    @staticmethod
    def _merge_type_counts(left: Dict[str, Any] | None, right: Dict[str, Any] | None) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for source in (left or {}, right or {}):
            if not isinstance(source, dict):
                continue
            for key, value in source.items():
                out[str(key)] = out.get(str(key), 0) + int(value)
        return out

    @classmethod
    def _merge_child_security_summary(
        cls,
        child_summary: Dict[str, Any],
        input_entities_summary: Dict[str, Any],
        tool_entities_summary: list[Dict[str, Any]],
        tool_calls_history: list[Dict[str, Any]],
        parent_tool_name: str | None,
        tool_inputs_protected: bool,
        tool_outputs_protected: bool,
    ) -> tuple[bool, bool]:
        input_redaction = child_summary.get("input_redaction") if isinstance(child_summary, dict) else {}
        input_entities = input_redaction.get("entities_detected") if isinstance(input_redaction, dict) else {}
        if isinstance(input_entities, dict):
            input_entities_summary["count"] = int(input_entities_summary.get("count", 0)) + int(input_entities.get("count", 0))
            types = input_entities_summary.get("types", {})
            if not isinstance(types, dict):
                types = {}
            for key, value in (input_entities.get("types") or {}).items():
                types[str(key)] = types.get(str(key), 0) + int(value)
            input_entities_summary["types"] = types

        tool_usage = child_summary.get("tool_usage") if isinstance(child_summary, dict) else {}
        if isinstance(tool_usage, dict):
            for call in tool_usage.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                name = call.get("name")
                if not isinstance(name, str) or not name:
                    continue
                merged_call = dict(call)
                if parent_tool_name and not merged_call.get("parent_tool"):
                    merged_call["parent_tool"] = parent_tool_name
                    merged_call["scope"] = f"{parent_tool_name}.subagent"
                else:
                    merged_call["scope"] = merged_call.get("scope") or "root"
                tool_calls_history.append(merged_call)
            if tool_usage.get("sensitive_inputs_protected"):
                tool_inputs_protected = True
            if tool_usage.get("sensitive_outputs_protected"):
                tool_outputs_protected = True
            for row in tool_usage.get("entities_detected_in_tool_outputs") or []:
                if isinstance(row, dict):
                    tool_entities_summary.append(row)

        return tool_inputs_protected, tool_outputs_protected

    def _placeholderize_tool_output(
        self,
        tool_output: Any,
        placeholder_map: Dict[str, str],
        tool_name: str | None = None,
    ) -> Any:
        if isinstance(tool_output, dict):
            # If this looks like a tool-result envelope, only scan the result payload.
            if "result" in tool_output and any(k in tool_output for k in ("id", "name", "isError")):
                sanitized = dict(tool_output)
                sanitized["result"], tool_pii = self._placeholderize_tool_output(
                    tool_output.get("result"),
                    placeholder_map,
                    tool_name=tool_name or tool_output.get("name"),
                )
                return sanitized, tool_pii
            sanitized: Dict[str, Any] = {}
            tool_pii = None
            for k, v in tool_output.items():
                sanitized_value, pii_info = self._placeholderize_tool_output(
                    v,
                    placeholder_map,
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
                self.components.pii.normalize(self.components.pii.detect(tool_output))
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
        normalized_tool_calls: list[Dict[str, Any]] = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            name = tc.get("name")
            if not isinstance(name, str) or not name:
                continue
            normalized_tool_calls.append({
                "name": name,
                "parent_tool": tc.get("parent_tool"),
                "scope": tc.get("scope") or ("root" if not tc.get("parent_tool") else f"{tc.get('parent_tool')}.subagent"),
            })
        return {
            "input_redaction": {
                "entities_detected": input_entities,
                "sensitive_detected": bool(input_entities.get("count")),
            },
            "tool_usage": {
                "tool_calls": normalized_tool_calls,
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
    def _has_agentic_metadata(value: Any) -> bool:
        if isinstance(value, dict):
            if any(k in value for k in ("placeholder_map", "placeholderMap", "security_summary", "securitySummary")):
                return True
            return any(SecureRuntime._has_agentic_metadata(v) for v in value.values())
        if isinstance(value, list):
            return any(SecureRuntime._has_agentic_metadata(item) for item in value)
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
