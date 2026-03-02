import json
import os
import re
import sys
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from dymium import DelegatedTransport, RuntimeConfig, SecureRuntime
from dymium.integrations.llamaindex import create_sanitized_agent_workflow
from dymium.sanitization import Sanitizer, SanitizationContext
from dymium.redaction import RedactionEngine
from dymium.detectors.pii import PresidioDetector


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"Missing env var: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def _require_reachable(url: str) -> None:
    import requests

    try:
        resp = requests.get(url.rstrip("/") + "/health", timeout=5)
        resp.raise_for_status()
    except Exception as exc:
        print(f"Presidio not reachable at {url}: {exc}", file=sys.stderr)
        sys.exit(1)


MAIN_CALLS: list[tuple[str, dict[str, Any]]] = []
SUB_CALLS: list[tuple[str, dict[str, Any]]] = []
REMOTE_CHILD_REQUESTS: list[dict[str, Any]] = []
REMOTE_AGENT_CALLS: list[tuple[str, dict[str, Any]]] = []
PLACEHOLDER_RE = re.compile(r"PH_[A-Z]+_[A-Z0-9]{5}")
PROTECTED_DIRECT_ARGS = {("lookup_customer", "email")}


class RemoteRuntimeHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003 - BaseHTTPRequestHandler signature
        return

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            REMOTE_CHILD_REQUESTS.append(payload)

        runtime: SecureRuntime | None = getattr(self.server, "dymium_runtime", None)
        if runtime is None:
            self.send_response(500)
            self.end_headers()
            return

        delegated_args = payload.get("delegated_arguments") if isinstance(payload, dict) else {}
        if not isinstance(delegated_args, dict):
            delegated_args = {}
        handoff_request = delegated_args.get("handoff_request")
        if not isinstance(handoff_request, str) or not handoff_request.strip():
            handoff_request = "Handle this escalation and return ticket_id and case_id."
        customer_email = str(delegated_args.get("customer_email", ""))
        customer_phone = str(delegated_args.get("customer_phone", ""))
        recursion_limit = payload.get("recursion_limit") if isinstance(payload, dict) else None

        remote_prompt = (
            "You are the remote escalation specialist.\n"
            "Use tools in order:\n"
            "1) remote_lookup_case(customer_email, customer_phone)\n"
            "2) remote_notify_ops(case_id from step 1, customer_email, customer_phone)\n"
            "Return a concise summary including case_id and ticket_id.\n\n"
            f"handoff_request={handoff_request}\n"
            f"customer_email={customer_email}\n"
            f"customer_phone={customer_phone}"
        )
        request_payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": remote_prompt}],
            "placeholderMap": payload.get("placeholderMap") if isinstance(payload, dict) else None,
            "recursion_limit": int(recursion_limit) if isinstance(recursion_limit, int) else 4,
        }
        result = runtime.invoke(request_payload)
        encoded = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class RemoteRuntimeServer:
    def __init__(self, *, api_key: str, model: str, presidio_url: str) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.invoke_url: str | None = None
        self._api_key = api_key
        self._model = model
        self._presidio_url = presidio_url

    def start(self) -> None:
        remote_model = self._model.split(":", 1)[1] if ":" in self._model else self._model
        remote_runtime_model = self._model if ":" in self._model else f"openai:{self._model}"

        def remote_lookup_case(customer_email: str, customer_phone: str) -> dict[str, Any]:
            REMOTE_AGENT_CALLS.append(("remote_lookup_case", {
                "customer_email": customer_email,
                "customer_phone": customer_phone,
            }))
            return {
                "case_id": "CASE-7788",
                "queue": "west-coast-escalations",
                "customer_email": customer_email,
                "customer_phone": customer_phone,
            }

        def remote_notify_ops(case_id: str, customer_email: str, customer_phone: str) -> dict[str, Any]:
            REMOTE_AGENT_CALLS.append(("remote_notify_ops", {
                "case_id": case_id,
                "customer_email": customer_email,
                "customer_phone": customer_phone,
            }))
            return {
                "ticket_id": "RM-7788",
                "case_id": case_id,
                "ops_contact_email": "escalations@carrier-ops.example",
                "customer_email": customer_email,
                "customer_phone": customer_phone,
            }

        remote_runtime = SecureRuntime.from_config(RuntimeConfig(
            model=remote_runtime_model,
            pii="presidio",
            llm_config={"api_key": self._api_key, "model": remote_model},
            pii_config={"base_url": self._presidio_url},
            tools=[
                {
                    "name": "remote_lookup_case",
                    "description": "Create or lookup escalation case for customer contact.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "customer_email": {"type": "string"},
                            "customer_phone": {"type": "string"},
                        },
                        "required": ["customer_email", "customer_phone"],
                    },
                    "handler": remote_lookup_case,
                },
                {
                    "name": "remote_notify_ops",
                    "description": "Notify remote ops queue and open callback ticket.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "case_id": {"type": "string"},
                            "customer_email": {"type": "string"},
                            "customer_phone": {"type": "string"},
                        },
                        "required": ["case_id", "customer_email", "customer_phone"],
                    },
                    "handler": remote_notify_ops,
                },
            ],
        ))
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), RemoteRuntimeHandler)
        setattr(self._server, "dymium_runtime", remote_runtime)
        host, port = self._server.server_address
        self.invoke_url = f"http://{host}:{port}/invoke"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._server:
            return
        self._server.shutdown()
        if self._thread:
            self._thread.join(timeout=5)


def main() -> None:
    api_key = _require_env("OPENAI_API_KEY")
    presidio_url = os.getenv("PRESIDIO_URL", "http://localhost:5000")
    _require_reachable(presidio_url)

    try:
        from llama_index.llms.openai import OpenAI as LlamaOpenAI
    except Exception as exc:
        print(f"Missing dependency: {exc}", file=sys.stderr)
        print("Install with: pip install llama-index llama-index-llms-openai", file=sys.stderr)
        sys.exit(1)
    remote_server = RemoteRuntimeServer(api_key=api_key, model=os.getenv("OPENAI_MODEL", "gpt-5"), presidio_url=presidio_url)
    remote_server.start()
    if not remote_server.invoke_url:
        print("Missing remote runtime invoke URL.", file=sys.stderr)
        sys.exit(1)
    remote_transport = DelegatedTransport(
        {
            "kind": "http",
            "url": remote_server.invoke_url,
            "prompt_arg": "handoff_request",
            "timeout_s": 90,
            "recursion_limit": 4,
        },
        name="run_remote_specialist",
    )

    sanitizer = Sanitizer(
        pii=PresidioDetector(base_url=presidio_url),
        redaction=RedactionEngine(),
    )
    ctx = SanitizationContext()

    llm = LlamaOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-5"))

    def lookup_customer(email: str) -> dict:
        """Lookup customer by email."""
        MAIN_CALLS.append(("lookup_customer", {"email": email}))
        return {
            "customer_id": "CUST-1001",
            "phone": "415-555-0135",
            "email": email,
            "alt_email": "alice.alt@example.com",
        }

    def list_recent_orders(customer_id: str) -> dict:
        """List recent orders for a customer."""
        MAIN_CALLS.append(("list_recent_orders", {"customer_id": customer_id}))
        return {
            "orders": [
                {"order_id": "ORD-9001", "total": "$120.00"},
                {"order_id": "ORD-9000", "total": "$75.00"},
            ]
        }

    def get_order_details(order_id: str) -> dict:
        """Get detailed order information including tracking and ship contact."""
        MAIN_CALLS.append(("get_order_details", {"order_id": order_id}))
        return {
            "order_id": order_id,
            "tracking_number": "1Z999AA10123456784",
            "warehouse_phone": "415-555-0199",
            "ship_address": "123 Market St, San Francisco, CA",
        }

    def get_shipping_status(order_id: str, phone: str) -> dict:
        """Get shipping status for an order and phone on file."""
        MAIN_CALLS.append(("get_shipping_status", {"order_id": order_id, "phone": phone}))
        return {
            "order_id": order_id,
            "status": "in_transit",
            "phone": phone,
        }

    def get_carrier_contact(tracking_number: str) -> dict:
        """Get carrier contact info for a tracking number."""
        MAIN_CALLS.append(("get_carrier_contact", {"tracking_number": tracking_number}))
        return {
            "tracking_number": tracking_number,
            "carrier": "UPS",
            "carrier_phone": "800-555-0100",
        }

    def request_eta(tracking_number: str, carrier_phone: str, customer_phone: str) -> dict:
        """Request ETA from carrier for a tracking number."""
        MAIN_CALLS.append(("request_eta", {
            "tracking_number": tracking_number,
            "carrier_phone": carrier_phone,
            "customer_phone": customer_phone,
        }))
        return {
            "tracking_number": tracking_number,
            "eta": "2026-02-15",
            "carrier_phone": carrier_phone,
        }

    def run_carrier_specialist(
        tracking_number: str,
        customer_phone: str,
        customer_email: str,
        dymium_context: dict[str, Any] | None = None,
    ) -> dict:
        """Delegated specialist: resolves placeholder inputs and runs sub-operations."""
        MAIN_CALLS.append(("run_carrier_specialist", {
            "tracking_number": tracking_number,
            "customer_phone": customer_phone,
            "customer_email": customer_email,
        }))
        placeholder_map = {}
        if isinstance(dymium_context, dict):
            raw_map = dymium_context.get("placeholder_map")
            if isinstance(raw_map, dict):
                placeholder_map = {str(k): str(v) for k, v in raw_map.items()}

        def resolve(value: str) -> str:
            return str(placeholder_map.get(value, value))

        SUB_CALLS.append(("lookup_carrier_sla", {"tracking_number": tracking_number}))
        SUB_CALLS.append(("open_carrier_callback", {
            "carrier_phone": "800-555-0100",
            "customer_email": resolve(customer_email),
        }))

        child_summary = {
            "input_redaction": {
                "entities_detected": {"count": 0, "types": {}},
                "sensitive_detected": False,
            },
            "tool_usage": {
                "tool_calls": [
                    {"name": "lookup_carrier_sla", "scope": "root", "parent_tool": None},
                    {"name": "open_carrier_callback", "scope": "root", "parent_tool": None},
                ],
                "sensitive_inputs_protected": True,
                "sensitive_outputs_protected": False,
                "entities_detected_in_tool_outputs": [],
            },
        }
        if isinstance(dymium_context, dict):
            dymium_context["placeholder_map"] = placeholder_map
            dymium_context["security_summary"] = child_summary

        return {
            "ticket_id": "CB-2210",
            "placeholder_map": placeholder_map,
            "security_summary": child_summary,
        }

    def run_remote_specialist(
        handoff_request: str,
        customer_email: str,
        customer_phone: str,
        dymium_context: dict[str, Any] | None = None,
    ) -> dict:
        """Delegate escalation to a simulated remote specialist runtime."""
        MAIN_CALLS.append(("run_remote_specialist", {
            "handoff_request": handoff_request,
            "customer_email": customer_email,
            "customer_phone": customer_phone,
        }))
        return remote_transport.invoke(
            {
                "handoff_request": handoff_request,
                "customer_email": customer_email,
                "customer_phone": customer_phone,
            },
            dymium_context=dymium_context,
        )

    workflow = create_sanitized_agent_workflow(
        tools_or_functions=[
            lookup_customer,
            list_recent_orders,
            get_order_details,
            get_shipping_status,
            get_carrier_contact,
            request_eta,
            run_carrier_specialist,
            run_remote_specialist,
        ],
        llm=llm,
        sanitizer=sanitizer,
        ctx=ctx,
        tool_types={
            "lookup_customer": "direct",
            "list_recent_orders": "direct",
            "get_order_details": "direct",
            "get_shipping_status": "direct",
            "get_carrier_contact": "direct",
            "request_eta": "direct",
            "run_carrier_specialist": "delegated",
            "run_remote_specialist": "delegated",
        },
        tool_direct_input_modes={"lookup_customer": "protect"},
    )

    async def _run() -> Any:
        return await workflow.run(
            "My email is alice@example.com. I need a full shipment summary for my most recent order.\n"
            "Please:\n"
            "1) Find my customer record.\n"
            "2) List my recent orders and pick the most recent.\n"
            "3) Get order details to retrieve the tracking number and ship contact.\n"
            "4) Use the tracking number to get the carrier contact phone.\n"
            "5) Use the carrier phone AND the phone on file to request an ETA.\n"
            "6) Delegate exception handling to run_carrier_specialist with tracking number, customer phone, and customer email.\n"
            "7) Delegate remote escalation to run_remote_specialist with a natural-language handoff_request, customer email, and customer phone.\n"
            "8) Check shipping status.\n"
            "Summarize order ID, status, ETA, tracking number, callback ticket, remote specialist ticket ID, and confirm which contact phone was used."
        )

    response = asyncio.run(_run())
    text = getattr(getattr(response, "response", None), "content", "") or str(response)

    print("Assistant (app-visible):")
    print(text)
    print("\nSecurity summary:")
    print(ctx.security_summary)

    if not MAIN_CALLS:
        print("\nFAIL: No tools were called. Ensure your model supports tool calling.", file=sys.stderr)
        sys.exit(1)

    print("\nVerification:")
    expected_tools = {
        "lookup_customer",
        "list_recent_orders",
        "get_order_details",
        "get_carrier_contact",
        "request_eta",
        "get_shipping_status",
        "run_carrier_specialist",
        "run_remote_specialist",
    }
    seen_tools = {name for name, _ in MAIN_CALLS}
    missing = expected_tools - seen_tools
    if missing:
        print(f"FAIL: Missing tool calls: {sorted(missing)}", file=sys.stderr)
    else:
        print("PASS: All expected tools were called.")

    def _fail_if_placeholder(value: str, label: str) -> bool:
        if PLACEHOLDER_RE.search(value):
            print(f"FAIL: Placeholder leaked into tool arg for {label}: {value}", file=sys.stderr)
            return True
        return False

    failures = 0
    protected_seen = False
    for name, args in MAIN_CALLS:
        if name in {"run_carrier_specialist", "run_remote_specialist"}:
            continue
        for key, value in args.items():
            if isinstance(value, str) and key in {"email", "phone", "customer_phone", "carrier_phone"}:
                if (name, key) in PROTECTED_DIRECT_ARGS:
                    if not PLACEHOLDER_RE.search(value):
                        print(
                            f"FAIL: protect mode did not preserve placeholder for {name}.{key}: {value}",
                            file=sys.stderr,
                        )
                        failures += 1
                    else:
                        protected_seen = True
                    continue
                if _fail_if_placeholder(value, f"{name}.{key}"):
                    failures += 1
    if not protected_seen:
        print("FAIL: Did not observe protected direct arg for lookup_customer.email.", file=sys.stderr)
        failures += 1

    specialist_args = next((a for n, a in MAIN_CALLS if n == "run_carrier_specialist"), None)
    if not specialist_args:
        print("FAIL: run_carrier_specialist not called.", file=sys.stderr)
        failures += 1
    elif not any(
        isinstance(v, str) and PLACEHOLDER_RE.search(v)
        for k, v in specialist_args.items()
        if k in {"customer_phone", "customer_email"}
    ):
        print("FAIL: run_carrier_specialist did not receive placeholderized sensitive args.", file=sys.stderr)
        failures += 1

    remote_args = next((a for n, a in MAIN_CALLS if n == "run_remote_specialist"), None)
    if not remote_args:
        print("FAIL: run_remote_specialist not called.", file=sys.stderr)
        failures += 1
    elif not any(
        isinstance(v, str) and PLACEHOLDER_RE.search(v)
        for k, v in remote_args.items()
        if k in {"customer_phone", "customer_email"}
    ):
        print("FAIL: run_remote_specialist did not receive placeholderized sensitive args.", file=sys.stderr)
        failures += 1

    for name, args in SUB_CALLS:
        for key, value in args.items():
            if isinstance(value, str) and key in {"customer_phone", "customer_email"} and PLACEHOLDER_RE.search(value):
                print(f"FAIL: Placeholder leaked into sub-agent arg for {name}.{key}: {value}", file=sys.stderr)
                failures += 1

    summary_calls = ((ctx.security_summary or {}).get("tool_usage") or {}).get("tool_calls") or []
    summary_tools = {call.get("name") for call in summary_calls if isinstance(call, dict)}
    expected_sub = {"lookup_carrier_sla", "open_carrier_callback"}
    missing_sub = expected_sub - summary_tools
    if missing_sub:
        print(f"FAIL: Missing sub-agent tool calls in security summary: {sorted(missing_sub)}", file=sys.stderr)
        failures += 1

    expected_remote_sub = {"remote_lookup_case", "remote_notify_ops"}
    missing_remote_sub = expected_remote_sub - summary_tools
    if missing_remote_sub:
        print(f"FAIL: Missing remote sub-agent tool calls in security summary: {sorted(missing_remote_sub)}", file=sys.stderr)
        failures += 1

    carrier_phone = "800-555-0100"
    req_eta = next((a for n, a in MAIN_CALLS if n == "request_eta"), None)
    if not req_eta:
        print("FAIL: request_eta not called.", file=sys.stderr)
        failures += 1
    elif req_eta.get("carrier_phone") != carrier_phone:
        print("FAIL: carrier_phone was not resolved for request_eta.", file=sys.stderr)
        failures += 1
    else:
        print("PASS: carrier_phone resolved for request_eta.")

    if PLACEHOLDER_RE.search(text):
        print("FAIL: placeholders leaked into final assistant response.", file=sys.stderr)
        failures += 1

    if not REMOTE_CHILD_REQUESTS:
        print("FAIL: Remote specialist runtime was not called.", file=sys.stderr)
        failures += 1
    else:
        child_payload = REMOTE_CHILD_REQUESTS[-1]
        child_map = child_payload.get("placeholderMap")
        if not isinstance(child_map, dict) or "alice@example.com" not in {str(v) for v in child_map.values()}:
            print("FAIL: Remote runtime did not receive propagated placeholderMap.", file=sys.stderr)
            failures += 1
        child_ctx = child_payload.get("dymium_context")
        child_ctx_map = child_ctx.get("placeholder_map") if isinstance(child_ctx, dict) else None
        if not isinstance(child_ctx_map, dict) or "alice@example.com" not in {str(v) for v in child_ctx_map.values()}:
            print("FAIL: Remote runtime did not receive propagated dymium_context.placeholder_map.", file=sys.stderr)
            failures += 1

    if not REMOTE_AGENT_CALLS:
        print("FAIL: Remote delegated runtime did not execute any remote tools.", file=sys.stderr)
        failures += 1

    remote_server.stop()
    if failures or missing:
        sys.exit(1)


if __name__ == "__main__":
    main()
