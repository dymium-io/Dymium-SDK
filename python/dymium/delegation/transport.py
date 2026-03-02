"""Delegated transport helpers for remote agent handoffs."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict
import urllib.error
import urllib.request


class DelegatedTransport:
    """Protocol-agnostic delegated transport config with HTTP implementation."""

    def __init__(self, config: Any, *, name: str | None = None) -> None:
        self._name = name or "delegated_tool"
        self._cfg = self._normalize_config(self._name, config)

    def as_tool_handler(self) -> Callable[..., Dict[str, Any]]:
        def _handler(**kwargs: Any) -> Dict[str, Any]:
            call_args = dict(kwargs)
            dymium_context = call_args.pop("dymium_context", None)
            return self.invoke(call_args, dymium_context=dymium_context)

        return _handler

    def invoke(
        self,
        args: Dict[str, Any] | None = None,
        *,
        dymium_context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        call_args = dict(args or {})
        if dymium_context is None:
            raw_ctx = call_args.pop("dymium_context", None)
            if isinstance(raw_ctx, dict):
                dymium_context = raw_ctx

        payload = self._build_request_payload(call_args, dymium_context)
        method = self._cfg["method"]
        url = self._cfg["url"]
        timeout_s = self._cfg["timeout_s"]
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **self._cfg["headers"],
        }

        req = urllib.request.Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Delegated transport {self._name!r} failed with {exc.code}: {body[:300]}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"Delegated transport {self._name!r} request failed: {exc}"
            ) from exc

        try:
            response_payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Delegated transport {self._name!r} returned non-JSON response"
            ) from exc

        if not isinstance(response_payload, dict):
            raise RuntimeError(
                f"Delegated transport {self._name!r} returned non-object response"
            )
        return response_payload

    def _build_request_payload(
        self,
        args: Dict[str, Any],
        dymium_context: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}

        messages_key = self._cfg["messages_arg"]
        prompt_key = self._cfg["prompt_arg"]
        messages_value = args.get(messages_key)
        if isinstance(messages_value, list):
            payload["messages"] = messages_value
        else:
            prompt = None
            for key in (prompt_key, "handoff_request", "query", "prompt", "message", "text"):
                value = args.get(key)
                if isinstance(value, str) and value.strip():
                    prompt = value
                    break
            if prompt is None:
                prompt = json.dumps(args, ensure_ascii=True, sort_keys=True)
            payload["messages"] = [{"role": "user", "content": prompt}]

        if self._cfg["include_args"]:
            payload["delegated_arguments"] = dict(args)

        recursion_limit = self._cfg["recursion_limit"]
        if recursion_limit is not None:
            payload["recursion_limit"] = recursion_limit

        if self._cfg["security_context"] != "off" and isinstance(dymium_context, dict):
            placeholder_map = dymium_context.get("placeholder_map")
            if isinstance(placeholder_map, dict) and placeholder_map:
                payload["placeholderMap"] = {str(k): str(v) for k, v in placeholder_map.items()}
            security_summary = dymium_context.get("security_summary")
            if isinstance(security_summary, dict):
                payload["security_summary"] = security_summary
            payload["dymium_context"] = dymium_context

        return payload

    @staticmethod
    def _normalize_config(name: str, config: Any) -> Dict[str, Any]:
        if isinstance(config, str):
            raw: Dict[str, Any] = {"url": config}
        elif isinstance(config, dict):
            raw = dict(config)
        else:
            raise ValueError(
                f"Tool {name!r} delegated_transport must be a URL string or object"
            )

        target = raw.get("target")
        if isinstance(target, dict):
            for key in (
                "kind",
                "url",
                "base_url",
                "baseUrl",
                "path",
                "method",
                "headers",
                "timeout",
                "timeout_s",
            ):
                if key in target and key not in raw:
                    raw[key] = target[key]

        kind = str(raw.get("kind") or "http").strip().lower()
        if kind != "http":
            raise ValueError(
                f"Tool {name!r} delegated_transport kind must be 'http'"
            )

        url = raw.get("url")
        if not isinstance(url, str) or not url.strip():
            base_url = raw.get("base_url") or raw.get("baseUrl")
            if isinstance(base_url, str) and base_url.strip():
                path = raw.get("path")
                path_part = str(path) if isinstance(path, str) and path else "/invoke"
                if not path_part.startswith("/"):
                    path_part = f"/{path_part}"
                url = f"{base_url.rstrip('/')}{path_part}"
        if not isinstance(url, str) or not url.strip():
            raise ValueError(
                f"Tool {name!r} delegated_transport requires 'url' or 'base_url'"
            )

        method = str(raw.get("method") or "POST").upper()
        if method != "POST":
            raise ValueError(
                f"Tool {name!r} delegated_transport method must be POST"
            )

        timeout_raw = raw.get("timeout_s")
        if timeout_raw is None:
            timeout_raw = raw.get("timeout")
        try:
            timeout_s = float(timeout_raw) if timeout_raw is not None else 30.0
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Tool {name!r} delegated_transport timeout must be numeric"
            ) from exc
        if timeout_s <= 0:
            raise ValueError(
                f"Tool {name!r} delegated_transport timeout must be positive"
            )

        headers_raw = raw.get("headers")
        headers: Dict[str, str] = {}
        if isinstance(headers_raw, dict):
            headers = {str(k): str(v) for k, v in headers_raw.items()}

        prompt_arg_raw = raw.get("prompt_arg")
        messages_arg_raw = raw.get("messages_arg")
        prompt_arg = str(prompt_arg_raw) if isinstance(prompt_arg_raw, str) and prompt_arg_raw else "handoff_request"
        messages_arg = str(messages_arg_raw) if isinstance(messages_arg_raw, str) and messages_arg_raw else "messages"
        include_args = bool(raw.get("include_args", True))

        recursion_limit = raw.get("recursion_limit")
        if recursion_limit is not None:
            try:
                recursion_limit = int(recursion_limit)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Tool {name!r} delegated_transport recursion_limit must be an integer"
                ) from exc
            if recursion_limit <= 0:
                raise ValueError(
                    f"Tool {name!r} delegated_transport recursion_limit must be positive"
                )

        security_context = str(raw.get("security_context") or "auto").strip().lower()
        if security_context not in {"auto", "off"}:
            raise ValueError(
                f"Tool {name!r} delegated_transport security_context must be 'auto' or 'off'"
            )

        return {
            "kind": kind,
            "url": url,
            "method": method,
            "timeout_s": timeout_s,
            "headers": headers,
            "prompt_arg": prompt_arg,
            "messages_arg": messages_arg,
            "include_args": include_args,
            "recursion_limit": recursion_limit,
            "security_context": security_context,
        }
