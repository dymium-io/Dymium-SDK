"""Delegated transport helpers for remote agent handoffs."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict
import urllib.error
import urllib.request


RUNTIME_CONTEXT_MARKER_KEY = "__dymium_runtime_context"
RUNTIME_CONTEXT_MARKER_VALUE = "v1"
RUNTIME_CONTEXT_ID_KEY = "__dymium_runtime_context_id"


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
        if isinstance(dymium_context, dict):
            marker = dymium_context.get(RUNTIME_CONTEXT_MARKER_KEY)
            if marker != RUNTIME_CONTEXT_MARKER_VALUE:
                raise ValueError(
                    "Delegated transport requires runtime-managed dymium_context. "
                    "Use SecureRuntime delegated_transport or integration wrappers."
                )
            if not isinstance(dymium_context.get(RUNTIME_CONTEXT_ID_KEY), str):
                raise ValueError(
                    "Delegated transport requires runtime-managed dymium_context id."
                )

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
        if self._cfg["security_context"] != "off" and isinstance(dymium_context, dict):
            self._merge_response_security_context(response_payload, dymium_context)
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
    def _merge_response_security_context(
        response_payload: Dict[str, Any],
        dymium_context: Dict[str, Any],
    ) -> None:
        updates, summary = DelegatedTransport._extract_response_security_context(
            response_payload,
            expected_context_id=dymium_context.get(RUNTIME_CONTEXT_ID_KEY),
        )
        if updates:
            current = dymium_context.get("placeholder_map")
            current_map = dict(current) if isinstance(current, dict) else {}
            current_map.update(updates)
            dymium_context["placeholder_map"] = current_map
        if isinstance(summary, dict):
            dymium_context["security_summary"] = summary

    @staticmethod
    def _extract_response_security_context(
        payload: Dict[str, Any],
        *,
        expected_context_id: Any,
    ) -> tuple[Dict[str, str], Dict[str, Any] | None]:
        if not isinstance(expected_context_id, str) or not expected_context_id:
            raise RuntimeError("Delegated transport is missing runtime context id.")

        dymium_ctx = payload.get("dymium_context")
        if not isinstance(dymium_ctx, dict):
            raise RuntimeError(
                "Delegated transport response must include dymium_context."
            )
        if dymium_ctx.get(RUNTIME_CONTEXT_MARKER_KEY) != RUNTIME_CONTEXT_MARKER_VALUE:
            raise RuntimeError("Delegated transport response dymium_context marker mismatch.")
        if dymium_ctx.get(RUNTIME_CONTEXT_ID_KEY) != expected_context_id:
            raise RuntimeError("Delegated transport response dymium_context id mismatch.")

        updates = DelegatedTransport._normalize_placeholder_map(dymium_ctx.get("placeholder_map"))
        summary = dymium_ctx.get("security_summary")
        return updates, (dict(summary) if isinstance(summary, dict) else None)

    @staticmethod
    def _normalize_placeholder_map(raw: Any) -> Dict[str, str]:
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
