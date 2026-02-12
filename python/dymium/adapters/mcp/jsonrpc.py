"""Minimal MCP JSON-RPC client for list_tools/call_tool."""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests


class MCPJsonRpcClient:
    def __init__(
        self,
        base_url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        client_name: str = "dymium-sdk",
        client_version: str = "0.1",
        protocol_version: str = "2024-11-05",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = dict(headers or {})
        self.headers.setdefault("Accept", "application/json")
        self.headers.setdefault("Content-Type", "application/json")
        self.timeout = timeout
        self.client_name = client_name
        self.client_version = client_version
        self.protocol_version = protocol_version
        self._id = 0
        self._initialized = False

    def initialize(self) -> Dict[str, Any]:
        if self._initialized:
            return {}
        params = {
            "protocolVersion": self.protocol_version,
            "capabilities": {"tools": {}},
            "clientInfo": {"name": self.client_name, "version": self.client_version},
        }
        result = self._request("initialize", params)
        self._initialized = True
        return result

    def list_tools(self) -> Dict[str, Any]:
        self.initialize()
        return self._request("tools/list", {})

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        self.initialize()
        return self._request("tools/call", {"name": name, "arguments": arguments})

    def _request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": params,
        }
        resp = requests.post(
            self.base_url,
            json=payload,
            headers=self.headers,
            timeout=self.timeout if self.timeout is not None else None,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"MCP error: {data['error']}")
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data if isinstance(data, dict) else {}
