"""Anthropic adapter (messages API)."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

import json
import requests


class AnthropicAdapter:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.anthropic.com",
        version: str = "2023-06-01",
        default_model: str = "claude-3-5-sonnet-20240620",
        max_tokens: int = 1024,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.version = version
        self.default_model = default_model
        self.max_tokens = max_tokens

    def chat(self, request: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._build_payload(request)
        url = f"{self.base_url}/v1/messages"
        resp = requests.post(url, json=payload, headers=self._headers())
        resp.raise_for_status()
        data = resp.json()

        tool_calls = self._extract_tool_calls(data)
        if tool_calls:
            return {"tool_calls": tool_calls}

        text = self._extract_text(data)
        return {"content": text, "raw": data}

    def stream(self, request: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        raise NotImplementedError

    def supports_tool_calling(self) -> bool:
        return True

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.version,
        }

    def _build_payload(self, request: Dict[str, Any]) -> Dict[str, Any]:
        messages = request.get("messages")
        if not messages:
            raise ValueError("AnthropicAdapter requires 'messages' in the request")

        system_parts = [m.get("content", "") for m in messages if m.get("role") == "system"]
        system = "\n".join([p for p in system_parts if p])

        anthropic_messages: List[Dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content") or ""

            if role == "system":
                continue
            if role == "tool":
                # Map tool result to Anthropic tool_result block
                tool_call_id = msg.get("tool_call_id") or msg.get("tool_call_id".upper()) or msg.get("tool_call_id")
                tool_name = msg.get("name")
                block = {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": str(content),
                }
                anthropic_messages.append({"role": "user", "content": [block]})
                continue

            if role == "assistant" and msg.get("tool_calls"):
                blocks = []
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    name = fn.get("name") or tc.get("name")
                    args = fn.get("arguments") or tc.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id"),
                        "name": name,
                        "input": args if isinstance(args, dict) else {},
                    })
                anthropic_messages.append({"role": "assistant", "content": blocks})
                continue

            anthropic_messages.append({"role": "assistant" if role == "assistant" else "user", "content": content})

        tools = request.get("tools")
        if tools:
            tools = self._normalize_tools(tools)

        payload: Dict[str, Any] = {
            "model": self.default_model,
            "max_tokens": self.max_tokens,
            "messages": anthropic_messages,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools
        return payload

    @staticmethod
    def _normalize_tools(tools: Any) -> Any:
        if not isinstance(tools, list):
            return tools
        # Convert canonical tool defs -> Anthropic tools
        out = []
        for tool in tools:
            if not isinstance(tool, dict) or "name" not in tool:
                continue
            out.append({
                "name": tool.get("name"),
                "description": tool.get("description"),
                "input_schema": tool.get("parameters") or {},
            })
        return out

    @staticmethod
    def _extract_tool_calls(data: Dict[str, Any]) -> list[Dict[str, Any]]:
        if not isinstance(data, dict):
            return []
        content = data.get("content")
        if not isinstance(content, list):
            return []
        out = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            out.append({
                "id": block.get("id", ""),
                "name": block.get("name"),
                "arguments": block.get("input") or {},
            })
        return out

    @staticmethod
    def _extract_text(data: Dict[str, Any]) -> str:
        if not isinstance(data, dict):
            return ""
        content = data.get("content")
        if not isinstance(content, list):
            return ""
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join([p for p in parts if p])
