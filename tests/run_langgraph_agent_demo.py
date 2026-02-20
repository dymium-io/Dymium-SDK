import os
import re
import sys
from typing import Any, Optional, Dict

from langchain.agents import create_agent
from langchain.tools import tool

from dymium.integrations.langchain import DymiumMiddleware
from dymium.integrations.langgraph import create_sanitized_agent, DymiumMessagesState
from dymium.sanitization import Sanitizer
from dymium.redaction import RedactionEngine
from dymium.detectors.pii import PresidioDetector


GraphState = DymiumMessagesState


MAIN_CALLS: list[tuple[str, dict[str, Any]]] = []
SUB_CALLS: list[tuple[str, dict[str, Any]]] = []
PLACEHOLDER_RE = re.compile(r"PH_[A-Z]+_[A-Z0-9]{5}")


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
    _require_env("OPENAI_API_KEY")
    presidio_url = os.getenv("PRESIDIO_URL", "http://localhost:5000")
    _require_reachable(presidio_url)

    model = os.getenv("OPENAI_MODEL", "gpt-5")
    if ":" not in model:
        model = f"openai:{model}"

    sub_sanitizer = Sanitizer(
        pii=PresidioDetector(base_url=presidio_url),
        redaction=RedactionEngine(),
    )
    sub_middleware = DymiumMiddleware(
        sub_sanitizer,
        tool_types={
            "lookup_carrier_sla": "non_agentic",
            "open_carrier_callback": "non_agentic",
        },
    ).middleware()
    specialist_agent = create_agent(
        model=model,
        tools=[lookup_carrier_sla, open_carrier_callback],
        middleware=[sub_middleware],
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
            "lookup_customer": "non_agentic",
            "list_recent_orders": "non_agentic",
            "get_order_details": "non_agentic",
            "get_carrier_contact": "non_agentic",
            "request_eta": "non_agentic",
            "get_shipping_status": "non_agentic",
            "run_carrier_specialist": "agentic",
        },
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
                    "7) Check shipping status.\n"
                    "Summarize order ID, status, ETA, tracking number, and carrier specialist callback ticket."
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

    for name, args in MAIN_CALLS:
        if name == "run_carrier_specialist":
            continue
        for key, value in args.items():
            if isinstance(value, str) and key in {"email", "phone", "customer_phone", "carrier_phone"}:
                if PLACEHOLDER_RE.search(value):
                    print(
                        f"FAIL: Placeholder leaked into non-agentic main tool arg for {name}.{key}: {value}",
                        file=sys.stderr,
                    )
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
            print("FAIL: agentic tool did not receive placeholderized sensitive args.", file=sys.stderr)
            failures += 1
        else:
            print("PASS: agentic tool received placeholderized sensitive args.")

    for name, args in SUB_CALLS:
        for key, value in args.items():
            if isinstance(value, str) and key in {"carrier_phone", "customer_email"} and PLACEHOLDER_RE.search(value):
                print(
                    f"FAIL: Placeholder leaked into sub-agent non-agentic tool arg for {name}.{key}: {value}",
                    file=sys.stderr,
                )
                failures += 1

    if isinstance(content, str) and PLACEHOLDER_RE.search(content):
        print("\nFAIL: placeholders leaked into final assistant response.", file=sys.stderr)
        failures += 1

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
