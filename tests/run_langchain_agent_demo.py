import os
import re
import sys
from typing import Any, Optional, Dict

from langchain.agents import create_agent
from langchain.tools import tool

from dymium.integrations.langchain import DymiumMiddleware
from dymium.sanitization import Sanitizer
from dymium.redaction import RedactionEngine
from dymium.detectors.pii import PresidioDetector


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
PLACEHOLDER_RE = re.compile(r"PH_[A-Z]+_[A-Z0-9]{5}")
PROTECTED_DIRECT_ARGS = {("lookup_customer", "email")}


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
def get_shipping_status(order_id: str, phone: str) -> dict:
    """Get shipping status for an order and phone on file."""
    MAIN_CALLS.append(("get_shipping_status", {"order_id": order_id, "phone": phone}))
    return {
        "order_id": order_id,
        "status": "in_transit",
        "phone": phone,
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
def lookup_carrier_sla(tracking_number: str) -> dict:
    """Lookup carrier service-level details for a tracking number."""
    SUB_CALLS.append(("lookup_carrier_sla", {"tracking_number": tracking_number}))
    return {
        "tracking_number": tracking_number,
        "carrier_phone": "800-555-0100",
        "promised_window": "Tomorrow 2pm-6pm",
        "escalation_cutoff": "2026-02-14T18:00:00Z",
    }


@tool
def assess_delivery_exception(tracking_number: str, ship_address: str) -> dict:
    """Assess whether this shipment needs hold/intercept handling."""
    SUB_CALLS.append(("assess_delivery_exception", {
        "tracking_number": tracking_number,
        "ship_address": ship_address,
    }))
    return {
        "tracking_number": tracking_number,
        "risk_level": "high",
        "recommended_action": "hold_at_location",
        "hold_location_id": "UPS-SF-018",
    }


@tool
def open_carrier_callback(carrier_phone: str, customer_email: str, callback_window: str) -> dict:
    """Open a callback ticket with the carrier support desk."""
    SUB_CALLS.append(("open_carrier_callback", {
        "carrier_phone": carrier_phone,
        "customer_email": customer_email,
        "callback_window": callback_window,
    }))
    return {
        "ticket_id": "CB-2210",
        "carrier_phone": carrier_phone,
        "customer_email": customer_email,
        "callback_window": callback_window,
    }


@tool
def create_hold_request(tracking_number: str, hold_location_id: str, customer_phone: str) -> dict:
    """Create a hold-at-location request with the carrier."""
    SUB_CALLS.append(("create_hold_request", {
        "tracking_number": tracking_number,
        "hold_location_id": hold_location_id,
        "customer_phone": customer_phone,
    }))
    return {
        "hold_request_id": "HOLD-7335",
        "tracking_number": tracking_number,
        "hold_location_id": hold_location_id,
        "customer_phone": customer_phone,
    }


@tool
def enroll_delivery_alerts(order_id: str, customer_phone: str, customer_email: str) -> dict:
    """Enroll customer in delivery exception alerts for this order."""
    SUB_CALLS.append(("enroll_delivery_alerts", {
        "order_id": order_id,
        "customer_phone": customer_phone,
        "customer_email": customer_email,
    }))
    return {
        "order_id": order_id,
        "alerts_subscription_id": "ALT-9042",
    }


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value and name == "OPENAI_API_KEY":
        for path in (
            "/home/dennis/Work/openai_api_key.txt",
            os.path.expanduser("~/Work/openai_api_key.txt"),
            os.path.join(os.path.dirname(__file__), "..", "..", "openai_api_key.txt"),
        ):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    value = fh.read().strip()
                if value:
                    os.environ[name] = value
                    break
            except OSError:
                continue
    if not value:
        print(f"Missing env var: {name}", file=sys.stderr)
        sys.exit(1)
    return value


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
            "lookup_carrier_sla": "direct",
            "assess_delivery_exception": "direct",
            "open_carrier_callback": "direct",
            "create_hold_request": "direct",
            "enroll_delivery_alerts": "direct",
        },
    ).middleware()
    specialist_agent = create_agent(
        model=model,
        tools=[
            lookup_carrier_sla,
            assess_delivery_exception,
            open_carrier_callback,
            create_hold_request,
            enroll_delivery_alerts,
        ],
        middleware=[sub_middleware],
    )

    @tool
    def run_carrier_specialist(
        order_id: str,
        tracking_number: str,
        ship_address: str,
        customer_phone: str,
        customer_email: str,
        handoff_request: str,
        dymium_context: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Delegate delivery exception handling to a specialist sub-agent."""
        MAIN_CALLS.append(("run_carrier_specialist", {
            "order_id": order_id,
            "tracking_number": tracking_number,
            "ship_address": ship_address,
            "customer_phone": customer_phone,
            "customer_email": customer_email,
            "handoff_request": handoff_request,
        }))
        _ = dymium_context  # injected by middleware; parent/child propagation is automatic.

        sub_result = specialist_agent.invoke({
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Main orchestrator handoff:\n"
                        f"{handoff_request}\n\n"
                        "You are the delivery-exception specialist. Use ALL tools to complete triage and mitigation:\n"
                        "1) lookup_carrier_sla(tracking_number)\n"
                        "2) assess_delivery_exception(tracking_number, ship_address)\n"
                        "3) open_carrier_callback(carrier_phone from SLA, customer_email, promised_window from SLA)\n"
                        "4) create_hold_request(tracking_number, hold_location_id from assessment, customer_phone)\n"
                        "5) enroll_delivery_alerts(order_id, customer_phone, customer_email)\n"
                        "Return a compact JSON-like summary with ticket_id, hold_request_id, alerts_subscription_id, "
                        "promised_window, and recommended_action.\n\n"
                        f"order_id={order_id}\n"
                        f"tracking_number={tracking_number}\n"
                        f"ship_address={ship_address}\n"
                        f"customer_phone={customer_phone}\n"
                        f"customer_email={customer_email}"
                    ),
                }
            ]
        })
        sub_messages = sub_result.get("messages", []) if isinstance(sub_result, dict) else []
        sub_text = _extract_last_assistant(sub_messages)
        if not sub_text and isinstance(sub_result, dict):
            sub_text = sub_result.get("text", "") or ""

        return {
            "specialist_summary": sub_text,
        }

    sanitizer = Sanitizer(
        pii=PresidioDetector(base_url=presidio_url),
        redaction=RedactionEngine(),
    )
    middleware = DymiumMiddleware(
        sanitizer,
        tool_types={
            "lookup_customer": "direct",
            "list_recent_orders": "direct",
            "get_order_details": "direct",
            "get_shipping_status": "direct",
            "get_carrier_contact": "direct",
            "request_eta": "direct",
            "run_carrier_specialist": "delegated",
        },
        tool_direct_input_modes={"lookup_customer": "protect"},
    ).middleware()

    agent = create_agent(
        model=model,
        tools=[
            lookup_customer,
            list_recent_orders,
            get_order_details,
            get_shipping_status,
            get_carrier_contact,
            request_eta,
            run_carrier_specialist,
        ],
        middleware=[middleware],
    )

    inputs = {
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
                    "6) Delegate delivery exception handling to run_carrier_specialist with order_id, tracking number, "
                    "ship_address, customer phone, customer email, and a natural-language handoff_request written "
                    "as if you are handing off to an operations specialist.\n"
                    "7) Check shipping status.\n"
                    "Summarize order ID, status, ETA, tracking number, callback ticket, hold request ID, and alerts subscription ID."
                ),
            }
        ]
    }

    result = agent.invoke(inputs)

    messages = result.get("messages", []) if isinstance(result, dict) else []
    assistant_text = _extract_last_assistant(messages)
    if not assistant_text and isinstance(result, dict):
        assistant_text = result.get("text", "") or ""

    print("Assistant (app-visible):")
    print(assistant_text)
    print("\nSecurity summary:")
    print(result.get("security_summary"))

    if not MAIN_CALLS:
        print("\nFAIL: No tools were called. Ensure your model supports tool calling.", file=sys.stderr)
        sys.exit(1)

    print("\nVerification:")
    failures = 0

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

    expected_sub = {
        "lookup_carrier_sla",
        "assess_delivery_exception",
        "open_carrier_callback",
        "create_hold_request",
        "enroll_delivery_alerts",
    }
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
        if name == "run_carrier_specialist":
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
            print(
                "FAIL: delegated tool did not receive placeholderized sensitive args.",
                file=sys.stderr,
            )
            failures += 1
        else:
            print("PASS: delegated tool received placeholderized sensitive args.")
        handoff_value = specialist_args.get("handoff_request")
        if not isinstance(handoff_value, str) or len(handoff_value.strip().split()) < 8:
            print("FAIL: handoff_request was not a meaningful natural-language request.", file=sys.stderr)
            failures += 1
        else:
            print("PASS: handoff_request natural-language handoff was provided to sub-agent.")

    for name, args in SUB_CALLS:
        for key, value in args.items():
            if isinstance(value, str) and key in {
                "carrier_phone",
                "customer_email",
                "customer_phone",
            } and PLACEHOLDER_RE.search(value):
                print(
                    f"FAIL: Placeholder leaked into sub-agent direct tool arg for {name}.{key}: {value}",
                    file=sys.stderr,
                )
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

    if PLACEHOLDER_RE.search(assistant_text):
        print("FAIL: placeholders leaked into final assistant response.", file=sys.stderr)
        failures += 1

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
