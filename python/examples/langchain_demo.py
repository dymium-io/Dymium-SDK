#!/usr/bin/env python3
"""LangChain + Dymium middleware example using Dymium HF detection.

Required env:
- OPENAI_API_KEY

Optional env:
- OPENAI_MODEL (default: gpt-5)
- DYMIUM_PII_MODEL (default: dymium/Dymium-NER-v1)
"""

from __future__ import annotations

import os
import sys

from langchain.agents import create_agent
from langchain.tools import tool

from dymium.detectors.pii import HuggingFacePIIDetector
from dymium.integrations.langchain import DymiumMiddleware
from dymium.sanitization import Sanitizer


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    print(f"Missing env var: {name}", file=sys.stderr)
    sys.exit(1)


@tool
def lookup_customer(email: str) -> dict[str, object]:
    """Lookup a customer account by email."""
    return {
        "customer_id": "CUST-1001",
        "email": email,
        "phone": "415-555-0135",
        "plan": "enterprise",
    }


@tool
def list_recent_orders(customer_id: str) -> dict[str, object]:
    """List recent orders for a customer account."""
    return {
        "customer_id": customer_id,
        "orders": [
            {"order_id": "ORD-9001", "total": "$120.00"},
            {"order_id": "ORD-9000", "total": "$75.00"},
        ],
    }


@tool
def get_order_details(order_id: str) -> dict[str, object]:
    """Get detailed order information including tracking."""
    return {
        "order_id": order_id,
        "tracking_number": "1Z999AA10123456784",
        "ship_address": "123 Market St, San Francisco, CA",
    }


@tool
def get_shipping_status(order_id: str, phone: str) -> dict[str, object]:
    """Return shipping status using order id and customer phone."""
    return {
        "order_id": order_id,
        "status": "in_transit",
        "phone": phone,
    }


@tool
def get_carrier_contact(tracking_number: str) -> dict[str, object]:
    """Get carrier support contact for a tracking number."""
    return {
        "tracking_number": tracking_number,
        "carrier": "UPS",
        "carrier_phone": "800-555-0100",
    }


@tool
def request_eta(tracking_number: str, carrier_phone: str, customer_phone: str) -> dict[str, object]:
    """Request delivery ETA from carrier."""
    return {
        "tracking_number": tracking_number,
        "carrier_phone": carrier_phone,
        "customer_phone": customer_phone,
        "eta": "2026-03-12",
    }


def lookup_carrier_sla(tracking_number: str) -> dict[str, object]:
    """Lookup carrier SLA details."""
    return {
        "tracking_number": tracking_number,
        "carrier_phone": "800-555-0100",
        "promised_window": "Tomorrow 2pm-6pm",
    }


def open_carrier_callback(carrier_phone: str, customer_email: str) -> dict[str, object]:
    """Open callback ticket with carrier support."""
    return {
        "carrier_phone": carrier_phone,
        "customer_email": customer_email,
        "ticket_id": "TCK-4412",
    }


def _extract_last_assistant(messages: list[object]) -> str:
    for msg in reversed(messages):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role not in {"assistant", "ai"}:
            role = getattr(msg, "type", None)
        if role not in {"assistant", "ai"}:
            continue
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
            if text_parts:
                return "\n".join(text_parts)
    return ""


def main() -> None:
    _require_env("OPENAI_API_KEY")
    model_name = os.getenv("OPENAI_MODEL", "gpt-5")
    detector_model = os.getenv("DYMIUM_PII_MODEL", "dymium/Dymium-NER-v1")
    model = f"openai:{model_name}"

    pii = HuggingFacePIIDetector(model_id=detector_model)
    sanitizer = Sanitizer(pii=pii)

    @tool
    def run_carrier_specialist(
        tracking_number: str,
        customer_phone: str,
        customer_email: str,
    ) -> dict[str, object]:
        """Delegate carrier exception handling to a local specialist tool."""
        sla = lookup_carrier_sla(tracking_number)
        callback = open_carrier_callback(
            carrier_phone=str(sla.get("carrier_phone", "800-555-0100")),
            customer_email=customer_email,
        )

        return {
            "specialist_summary": (
                f"Carrier callback ticket {callback.get('ticket_id')} opened for "
                f"tracking {tracking_number}."
            ),
            "carrier_phone": callback.get("carrier_phone", "800-555-0100"),
            "ticket_id": callback.get("ticket_id", "TCK-4412"),
        }

    lookup_customer.metadata = {"dymium": {"tool_type": "direct", "input_mode": "protect"}}
    list_recent_orders.metadata = {"dymium": {"tool_type": "direct", "input_mode": "resolve"}}
    get_order_details.metadata = {"dymium": {"tool_type": "direct", "input_mode": "resolve"}}
    get_shipping_status.metadata = {"dymium": {"tool_type": "direct", "input_mode": "resolve"}}
    get_carrier_contact.metadata = {"dymium": {"tool_type": "direct", "input_mode": "resolve"}}
    request_eta.metadata = {"dymium": {"tool_type": "direct", "input_mode": "resolve"}}
    run_carrier_specialist.metadata = {"dymium": {"tool_type": "delegated"}}
    tools = [
        lookup_customer,
        list_recent_orders,
        get_order_details,
        get_shipping_status,
        get_carrier_contact,
        request_eta,
        run_carrier_specialist,
    ]
    middleware = DymiumMiddleware(sanitizer, tools=tools).middleware()

    agent = create_agent(
        model=model,
        tools=tools,
        middleware=[middleware],
    )

    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "My email is alice@example.com and my phone is 415-555-0135. "
                        "Find my account, identify my latest order, retrieve tracking info, "
                        "get carrier contact and ETA, and run carrier specialist escalation. "
                        "Return order id, status, tracking number, eta, and specialist summary."
                    ),
                }
            ]
        }
    )

    messages = result.get("messages", []) if isinstance(result, dict) else []
    print("Assistant (app-visible):")
    print(_extract_last_assistant(messages) or str(result))
    print("\nSecurity summary:")
    print(result.get("security_summary", {}))


if __name__ == "__main__":
    main()
