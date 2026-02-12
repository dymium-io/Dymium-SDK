"""OpenAI adapter (chat/completions + responses).

Intended to support OpenAI public API and OpenAI-compatible servers.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable

import json
import requests


class OpenAIAdapter:
    def __init__(
        self,
        api_key: str,
        default_model: str = "gpt-5",
        max_tokens: int | None = None,
        top_p: float | None = None,
        presence_penalty: float | None = None,
        frequency_penalty: float | None = None,
        stop: list[str] | str | None = None,
    ) -> None:
        self.api_key = api_key
        self.default_model = default_model
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.presence_penalty = presence_penalty
        self.frequency_penalty = frequency_penalty
        self.stop = stop
        self.last_request: Dict[str, Any] | None = None
        self.last_response: Dict[str, Any] | None = None

    def chat(self, request: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._build_chat_payload(request)
        url = "https://api.openai.com/v1/chat/completions"
        self.last_request = {"url": url, "payload": payload}
        resp = requests.post(url, json=payload, headers=self._headers())
        resp.raise_for_status()
        data = resp.json()
        self.last_response = data
        return data

    def stream(self, request: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        payload = self._build_chat_payload(request)
        payload["stream"] = True
        url = "https://api.openai.com/v1/chat/completions"
        with requests.post(url, json=payload, headers=self._headers(), stream=True) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith(b"data: "):
                    data = line[len(b"data: "):]
                else:
                    data = line
                if data == b"[DONE]":
                    break
                try:
                    yield json.loads(data.decode("utf-8"))
                except json.JSONDecodeError:
                    continue

    def supports_tool_calling(self) -> bool:
        return True

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _build_chat_payload(self, request: Dict[str, Any]) -> Dict[str, Any]:
        messages = request.get("messages")
        if not messages:
            raise ValueError("OpenAIAdapter requires 'messages' in the request")

        payload: Dict[str, Any] = {
            "model": self.default_model,
            "messages": messages,
        }

        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if self.top_p is not None:
            payload["top_p"] = self.top_p
        if self.presence_penalty is not None:
            payload["presence_penalty"] = self.presence_penalty
        if self.frequency_penalty is not None:
            payload["frequency_penalty"] = self.frequency_penalty
        if self.stop is not None:
            payload["stop"] = self.stop

        if "tools" in request:
            payload["tools"] = self._normalize_tools(request.get("tools"))
        if "tool_choice" in request:
            payload["tool_choice"] = request.get("tool_choice")

        return payload

    @staticmethod
    def _normalize_tools(tools: Any) -> Any:
        if not isinstance(tools, list):
            return tools
        if tools and isinstance(tools[0], dict) and ("type" in tools[0] or "function" in tools[0]):
            return tools
        out = []
        for tool in tools:
            if not isinstance(tool, dict) or "name" not in tool:
                continue
            fn = {"name": tool["name"]}
            if tool.get("description"):
                fn["description"] = tool["description"]
            if tool.get("parameters"):
                fn["parameters"] = tool["parameters"]
            out.append({"type": "function", "function": fn})
        return out
