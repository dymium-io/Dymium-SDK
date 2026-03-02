import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional, Dict

from langchain.agents import create_agent
from langchain.tools import tool

from dymium import DelegatedTransport, RuntimeConfig, SecureRuntime
from dymium.integrations.langchain import DymiumMiddleware
from dymium.integrations.langgraph import create_sanitized_agent, DymiumMessagesState
from dymium.sanitization import Sanitizer
from dymium.redaction import RedactionEngine
from dymium.detectors.pii import PresidioDetector


GraphState = DymiumMessagesState


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


@tool
def lookup_customer(email: str) -> dict:
    """Lookup customer by email."""
    MAIN_CALLS.append(("lookup_customer", {"email": email}))
    return {
        "customer_id": "CUST-1001",
        "phone": "415-555-0135",
        "email": email,
        "alt_email": "alice.alt@example.com",
    }


@tool
def list_recent_orders(customer_id: str) -> dict:
    """List recent orders for a customer."""
    MAIN_CALLS.append(("list_recent_orders", {"customer_id": customer_id}))
    return {
        "orders": [
            {"order_id": "ORD-9001", "total": "$120.00"},
            {"order_id": "ORD-9000", "total": "$75.00"},
        ]
    }


@tool
def get_order_details(order_id: str) -> dict:
    """Get detailed order information including tracking and ship contact."""
    MAIN_CALLS.append(("get_order_details", {"order_id": order_id}))
    return {
        "order_id": order_id,
        "tracking_number": "1Z999AA10123456784",
        "warehouse_phone": "415-555-0199",
        "ship_address": "123 Market St, San Francisco, CA",
    }


@tool
def get_carrier_contact(tracking_number: str) -> dict:
    """Get carrier contact info for a tracking number."""
    MAIN_CALLS.append(("get_carrier_contact", {"tracking_number": tracking_number}))
    return {
        "tracking_number": tracking_number,
        "carrier": "UPS",
        "carrier_phone": "800-555-0100",
    }


@tool
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


@tool
def get_shipping_status(order_id: str, phone: str) -> dict:
    """Get shipping status for an order and phone on file."""
    MAIN_CALLS.append(("get_shipping_status", {"order_id": order_id, "phone": phone}))
    return {
        "order_id": order_id,
        "status": "in_transit",
        "phone": phone,
    }


@tool
def lookup_carrier_sla(tracking_number: str) -> dict:
    """Lookup carrier service-level details for a tracking number."""
    SUB_CALLS.append(("lookup_carrier_sla", {"tracking_number": tracking_number}))
    return {
        "tracking_number": tracking_number,
        "carrier_phone": "800-555-0100",
        "promised_window": "Tomorrow 2pm-6pm",
    }


@tool
def open_carrier_callback(carrier_phone: str, customer_email: str) -> dict:
    """Open a callback ticket with the carrier support desk."""
    SUB_CALLS.append(("open_carrier_callback", {
        "carrier_phone": carrier_phone,
        "customer_email": customer_email,
    }))
    return {
        "ticket_id": "CB-2210",
        "carrier_phone": carrier_phone,
        "customer_email": customer_email,
    }


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


def _extract_last_assistant(messages: list[Any]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            return msg.get("content", "") or ""
        role = getattr(msg, "type", None) or getattr(msg, "role", None)
        if role in {"ai", "assistant"}:
            return getattr(msg, "content", "") or ""
    return ""


def main() -> None:
    api_key = _require_env("OPENAI_API_KEY")
    presidio_url = os.getenv("PRESIDIO_URL", "http://localhost:5000")
    _require_reachable(presidio_url)

    model = os.getenv("OPENAI_MODEL", "gpt-5")
    if ":" not in model:
        model = f"openai:{model}"
    remote_server = RemoteRuntimeServer(api_key=api_key, model=model, presidio_url=presidio_url)
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

    sub_sanitizer = Sanitizer(
        pii=PresidioDetector(base_url=presidio_url),
        redaction=RedactionEngine(),
    )
    sub_middleware = DymiumMiddleware(
        sub_sanitizer,
        tool_types={
            "lookup_carrier_sla": "direct",
            "open_carrier_callback": "direct",
        },
    ).middleware()
    specialist_agent = create_agent(
        model=model,
        tools=[lookup_carrier_sla, open_carrier_callback],
        middleware=[sub_middleware],
    )

    @tool
    def run_remote_specialist(
        handoff_request: str,
        customer_email: str,
        customer_phone: str,
        dymium_context: Optional[Dict[str, Any]] = None,
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

    @tool
    def run_carrier_specialist(
        tracking_number: str,
        customer_phone: str,
        customer_email: str,
        dymium_context: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Delegate carrier exception handling to a specialist sub-agent."""
        MAIN_CALLS.append(("run_carrier_specialist", {
            "tracking_number": tracking_number,
            "customer_phone": customer_phone,
            "customer_email": customer_email,
        }))
        placeholder_map = {}
        if isinstance(dymium_context, dict):
            placeholder_map = dict(dymium_context.get("placeholder_map") or {})

        sub_result = specialist_agent.invoke({
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "You are the carrier specialist. Use tools to do both tasks:\n"
                        "1) Lookup carrier SLA for tracking number.\n"
                        "2) Open a callback ticket with the carrier using the returned carrier phone "
                        "and the customer email.\n"
                        "Return ticket_id, promised_window, and carrier phone.\n\n"
                        f"tracking_number={tracking_number}\n"
                        f"customer_phone={customer_phone}\n"
                        f"customer_email={customer_email}"
                    ),
                }
            ],
            "placeholder_map": placeholder_map,
            "placeholderMap": placeholder_map,
        })
        sub_messages = sub_result.get("messages", []) if isinstance(sub_result, dict) else []
        sub_text = _extract_last_assistant(sub_messages)
        if not sub_text and isinstance(sub_result, dict):
            sub_text = sub_result.get("text", "") or ""
        sub_placeholder_map = sub_result.get("placeholder_map", {}) if isinstance(sub_result, dict) else {}
        sub_security_summary = sub_result.get("security_summary", {}) if isinstance(sub_result, dict) else {}
        if isinstance(dymium_context, dict):
            dymium_context["placeholder_map"] = sub_placeholder_map
            dymium_context["security_summary"] = sub_security_summary

        return {
            "specialist_summary": sub_text,
            "placeholder_map": sub_placeholder_map,
            "security_summary": sub_security_summary,
        }

    sanitizer = Sanitizer(
        pii=PresidioDetector(base_url=presidio_url),
        redaction=RedactionEngine(),
    )

    tools = [
        lookup_customer,
        list_recent_orders,
        get_order_details,
        get_carrier_contact,
        request_eta,
        get_shipping_status,
        run_carrier_specialist,
        run_remote_specialist,
    ]

    from langchain.chat_models import init_chat_model

    lc_model = init_chat_model(model)
    app = create_sanitized_agent(
        model=lc_model,
        tools=tools,
        sanitizer=sanitizer,
        state_schema=GraphState,
        max_tool_calls=12,
        tool_types={
            "lookup_customer": "direct",
            "list_recent_orders": "direct",
            "get_order_details": "direct",
            "get_carrier_contact": "direct",
            "request_eta": "direct",
            "get_shipping_status": "direct",
            "run_carrier_specialist": "delegated",
            "run_remote_specialist": "delegated",
        },
        tool_direct_input_modes={"lookup_customer": "protect"},
    )

    raw_inputs = {
        "messages": [
            {
                "role": "user",
                "content": (
                    "My email is alice@example.com. I need a full shipment summary for my most recent order.\n"
                    "Please:\n"
                    "1) Find my customer record.\n"
                    "2) List my recent orders and pick the most recent.\n"
                    "3) Get order details to retrieve the tracking number and ship contact.\n"
                    "4) Use the tracking number to get the carrier contact phone.\n"
                    "5) Use the carrier phone AND the phone on file to request an ETA.\n"
                    "6) Delegate carrier exception handling to run_carrier_specialist with tracking number, "
                    "customer phone, and customer email.\n"
                    "7) Delegate remote escalation to run_remote_specialist with a natural-language handoff_request, "
                    "customer email, and customer phone.\n"
                    "8) Check shipping status.\n"
                    "Summarize order ID, status, ETA, tracking number, carrier specialist callback ticket, and remote specialist ticket ID."
                ),
            }
        ]
    }

    result = app.invoke(raw_inputs, {"recursion_limit": 30})
    messages = result.get("messages", [])

    print("Assistant (app-visible):")
    last = messages[-1]
    content = last.get("content") if isinstance(last, dict) else getattr(last, "content", "")
    print(content)
    print("\nSecurity summary:")
    print(result.get("security_summary"))

    failures = 0
    if not MAIN_CALLS:
        print("\nFAIL: No tools were called. Ensure your model supports tool calling.", file=sys.stderr)
        failures += 1

    expected_main = {
        "lookup_customer",
        "list_recent_orders",
        "get_order_details",
        "get_carrier_contact",
        "request_eta",
        "get_shipping_status",
        "run_carrier_specialist",
        "run_remote_specialist",
    }
    seen_main = {name for name, _ in MAIN_CALLS}
    missing_main = expected_main - seen_main
    if missing_main:
        print(f"FAIL: Missing main tool calls: {sorted(missing_main)}", file=sys.stderr)
        failures += 1
    else:
        print("PASS: All expected main tools were called.")

    expected_sub = {"lookup_carrier_sla", "open_carrier_callback"}
    seen_sub = {name for name, _ in SUB_CALLS}
    missing_sub = expected_sub - seen_sub
    if missing_sub:
        print(f"FAIL: Missing sub-agent tool calls: {sorted(missing_sub)}", file=sys.stderr)
        failures += 1
    else:
        print("PASS: All expected sub-agent tools were called.")

    summary_calls = ((result.get("security_summary") or {}).get("tool_usage") or {}).get("tool_calls") or []
    summary_tools = {call.get("name") for call in summary_calls if isinstance(call, dict)}
    missing_in_summary = expected_sub - summary_tools
    if missing_in_summary:
        print(
            f"FAIL: Sub-agent tools missing from merged security summary: {sorted(missing_in_summary)}",
            file=sys.stderr,
        )
        failures += 1
    else:
        print("PASS: Sub-agent tools are present in merged security summary.")

    if not missing_in_summary:
        bad_parent = []
        for call in summary_calls:
            if not isinstance(call, dict):
                continue
            if call.get("name") in expected_sub and call.get("parent_tool") != "run_carrier_specialist":
                bad_parent.append(call)
        if bad_parent:
            print(
                "FAIL: Sub-agent tool call hierarchy is missing parent_tool=run_carrier_specialist.",
                file=sys.stderr,
            )
            failures += 1
        else:
            print("PASS: Sub-agent calls are tied to run_carrier_specialist in summary.")

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
                if PLACEHOLDER_RE.search(value):
                    print(
                        f"FAIL: Placeholder leaked into direct main tool arg for {name}.{key}: {value}",
                        file=sys.stderr,
                    )
                    failures += 1
    if not protected_seen:
        print("FAIL: Did not observe protected direct arg for lookup_customer.email.", file=sys.stderr)
        failures += 1

    specialist_args = next((a for n, a in MAIN_CALLS if n == "run_carrier_specialist"), None)
    if not specialist_args:
        print("FAIL: run_carrier_specialist not called.", file=sys.stderr)
        failures += 1
    else:
        if not any(
            isinstance(v, str) and PLACEHOLDER_RE.search(v)
            for k, v in specialist_args.items()
            if k in {"customer_phone", "customer_email"}
        ):
            print("FAIL: delegated tool did not receive placeholderized sensitive args.", file=sys.stderr)
            failures += 1
        else:
            print("PASS: delegated tool received placeholderized sensitive args.")

    remote_args = next((a for n, a in MAIN_CALLS if n == "run_remote_specialist"), None)
    if not remote_args:
        print("FAIL: run_remote_specialist not called.", file=sys.stderr)
        failures += 1
    else:
        if not any(
            isinstance(v, str) and PLACEHOLDER_RE.search(v)
            for k, v in remote_args.items()
            if k in {"customer_phone", "customer_email"}
        ):
            print("FAIL: run_remote_specialist did not receive placeholderized sensitive args.", file=sys.stderr)
            failures += 1

    for name, args in SUB_CALLS:
        for key, value in args.items():
            if isinstance(value, str) and key in {"carrier_phone", "customer_email"} and PLACEHOLDER_RE.search(value):
                print(
                    f"FAIL: Placeholder leaked into sub-agent direct tool arg for {name}.{key}: {value}",
                    file=sys.stderr,
                )
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

    expected_remote_sub = {"remote_lookup_case", "remote_notify_ops"}
    missing_remote_sub = expected_remote_sub - summary_tools
    if missing_remote_sub:
        print(
            f"FAIL: Remote sub-agent tools missing from merged security summary: {sorted(missing_remote_sub)}",
            file=sys.stderr,
        )
        failures += 1

    if isinstance(content, str) and PLACEHOLDER_RE.search(content):
        print("\nFAIL: placeholders leaked into final assistant response.", file=sys.stderr)
        failures += 1

    remote_server.stop()
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
