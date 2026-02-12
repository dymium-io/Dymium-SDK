"""Gemini adapter (Google AI / Vertex)."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

import json
import requests


class GeminiAdapter:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        default_model: str = "gemini-1.5-pro",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model

    def chat(self, request: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._build_payload(request)
        model = payload.pop("_model")
        url = f"{self.base_url}/models/{model}:generateContent?key={self.api_key}"
        resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"})
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

    def _build_payload(self, request: Dict[str, Any]) -> Dict[str, Any]:
        messages = request.get("messages")
        if not messages:
            raise ValueError("GeminiAdapter requires 'messages' in the request")

        system_parts = [m.get("content", "") for m in messages if m.get("role") == "system"]
        system_text = "\n".join([p for p in system_parts if p])

        contents: List[Dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content") or ""
            if role == "system":
                continue

            if role == "tool":
                tool_name = msg.get("name")
                tool_call_id = msg.get("tool_call_id")
                parts = [{
                    "functionResponse": {
                        "name": tool_name,
                        "response": {"id": tool_call_id, "content": str(content)},
                    }
                }]
                contents.append({"role": "user", "parts": parts})
                continue

            if role == "assistant" and msg.get("tool_calls"):
                parts = []
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    name = fn.get("name") or tc.get("name")
                    args = fn.get("arguments") or tc.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    parts.append({"functionCall": {"name": name, "args": args}})
                contents.append({"role": "model", "parts": parts})
                continue

            contents.append({"role": "model" if role == "assistant" else "user", "parts": [{"text": content}]})

        tools = request.get("tools")
        if tools:
            tools = self._normalize_tools(tools)

        payload: Dict[str, Any] = {
            "_model": self.default_model,
            "contents": contents,
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        if tools:
            payload["tools"] = tools
        return payload

    @staticmethod
    def _normalize_tools(tools: Any) -> Any:
        if not isinstance(tools, list):
            return tools
        out = []
        functions = []
        for tool in tools:
            if not isinstance(tool, dict) or "name" not in tool:
                continue
            functions.append({
                "name": tool.get("name"),
                "description": tool.get("description"),
                "parameters": tool.get("parameters") or {},
            })
        if functions:
            out.append({"functionDeclarations": functions})
        return out

    @staticmethod
    def _extract_tool_calls(data: Dict[str, Any]) -> list[Dict[str, Any]]:
        if not isinstance(data, dict):
            return []
        candidates = data.get("candidates") or []
        if not candidates:
            return []
        content = candidates[0].get("content", {})
        parts = content.get("parts") or []
        out = []
        for part in parts:
            if isinstance(part, dict) and "functionCall" in part:
                fc = part["functionCall"]
                out.append({
                    "id": fc.get("name", ""),
                    "name": fc.get("name"),
                    "arguments": fc.get("args") or {},
                })
        return out

    @staticmethod
    def _extract_text(data: Dict[str, Any]) -> str:
        if not isinstance(data, dict):
            return ""
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        content = candidates[0].get("content", {})
        parts = content.get("parts") or []
        texts = []
        for part in parts:
            if isinstance(part, dict) and "text" in part:
                texts.append(part["text"])
        return "\n".join([t for t in texts if t])
