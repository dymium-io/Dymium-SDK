import os
import re
import sys
import asyncio
from typing import Any

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
PLACEHOLDER_RE = re.compile(r"PH_[A-Z]+_[A-Z0-9]{5}")
PROTECTED_DIRECT_ARGS = {("lookup_customer", "email")}


def main() -> None:
    _require_env("OPENAI_API_KEY")
    presidio_url = os.getenv("PRESIDIO_URL", "http://localhost:5000")
    _require_reachable(presidio_url)

    try:
        from llama_index.llms.openai import OpenAI as LlamaOpenAI
    except Exception as exc:
        print(f"Missing dependency: {exc}", file=sys.stderr)
        print("Install with: pip install llama-index llama-index-llms-openai", file=sys.stderr)
        sys.exit(1)

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

    workflow = create_sanitized_agent_workflow(
        tools_or_functions=[
            lookup_customer,
            list_recent_orders,
            get_order_details,
            get_shipping_status,
            get_carrier_contact,
            request_eta,
            run_carrier_specialist,
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
            "7) Check shipping status.\n"
            "Summarize order ID, status, ETA, tracking number, callback ticket, and confirm which contact phone was used."
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

    if failures or missing:
        sys.exit(1)


if __name__ == "__main__":
    main()
