#!/usr/bin/env python3
"""SecureRuntime example with richer tool policy flow.

Required env:
- OPENAI_API_KEY

Optional env:
- OPENAI_MODEL (default: gpt-5)
- DYMIUM_PII_MODEL (default: dymium/Dymium-NER-v1)
"""

from __future__ import annotations

import os
import sys
from typing import Any

from dymium import RuntimeConfig, SecureRuntime


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    print(f"Missing env var: {name}", file=sys.stderr)
    sys.exit(1)


def lookup_customer(email: str) -> dict[str, Any]:
    return {
        "customer_id": "CUST-1001",
        "email": email,
        "phone": "415-555-0135",
        "plan": "enterprise",
    }


def list_recent_orders(customer_id: str) -> dict[str, Any]:
    return {
        "customer_id": customer_id,
        "orders": [
            {"order_id": "ORD-9001", "total": "$120.00"},
            {"order_id": "ORD-9000", "total": "$75.00"},
        ],
    }


def get_order_details(order_id: str) -> dict[str, Any]:
    return {
        "order_id": order_id,
        "tracking_number": "1Z999AA10123456784",
        "ship_address": "123 Market St, San Francisco, CA",
    }


def get_shipping_status(order_id: str, phone: str) -> dict[str, Any]:
    return {
        "order_id": order_id,
        "status": "in_transit",
        "phone": phone,
    }


def get_carrier_contact(tracking_number: str) -> dict[str, Any]:
    return {
        "tracking_number": tracking_number,
        "carrier": "UPS",
        "carrier_phone": "800-555-0100",
    }


def request_eta(tracking_number: str, carrier_phone: str, customer_phone: str) -> dict[str, Any]:
    return {
        "tracking_number": tracking_number,
        "carrier_phone": carrier_phone,
        "customer_phone": customer_phone,
        "eta": "2026-03-12",
    }


def lookup_carrier_sla(tracking_number: str) -> dict[str, Any]:
    return {
        "tracking_number": tracking_number,
        "carrier_phone": "800-555-0100",
        "promised_window": "Tomorrow 2pm-6pm",
    }


def open_carrier_callback(carrier_phone: str, customer_email: str) -> dict[str, Any]:
    return {
        "carrier_phone": carrier_phone,
        "customer_email": customer_email,
        "ticket_id": "TCK-4412",
    }


def run_carrier_specialist(
    tracking_number: str,
    customer_phone: str,
    customer_email: str,
) -> dict[str, Any]:
    """Delegated specialist: performs specialist operations locally."""
    _ = customer_phone
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


def main() -> None:
    api_key = _require_env("OPENAI_API_KEY")
    model_name = os.getenv("OPENAI_MODEL", "gpt-5")
    detector_model = os.getenv("DYMIUM_PII_MODEL", "dymium/Dymium-NER-v1")

    runtime = SecureRuntime.from_config(
        RuntimeConfig(
            model=f"openai:{model_name}",
            model_config={"api_key": api_key, "model": model_name},
            pii="dymium_hf",
            pii_config={"model_id": detector_model},
            tools=[
                {
                    "name": "lookup_customer",
                    "description": "Lookup customer by email.",
                    "tool_type": "direct",
                    "input_mode": "protect",
                    "parameters": {
                        "type": "object",
                        "properties": {"email": {"type": "string"}},
                        "required": ["email"],
                    },
                    "handler": lookup_customer,
                },
                {
                    "name": "list_recent_orders",
                    "description": "List recent orders for a customer.",
                    "tool_type": "direct",
                    "input_mode": "resolve",
                    "parameters": {
                        "type": "object",
                        "properties": {"customer_id": {"type": "string"}},
                        "required": ["customer_id"],
                    },
                    "handler": list_recent_orders,
                },
                {
                    "name": "get_order_details",
                    "description": "Get detailed order information including tracking.",
                    "tool_type": "direct",
                    "input_mode": "resolve",
                    "parameters": {
                        "type": "object",
                        "properties": {"order_id": {"type": "string"}},
                        "required": ["order_id"],
                    },
                    "handler": get_order_details,
                },
                {
                    "name": "get_shipping_status",
                    "description": "Return shipping status for an order.",
                    "tool_type": "direct",
                    "input_mode": "resolve",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "order_id": {"type": "string"},
                            "phone": {"type": "string"},
                        },
                        "required": ["order_id", "phone"],
                    },
                    "handler": get_shipping_status,
                },
                {
                    "name": "get_carrier_contact",
                    "description": "Get carrier support contact for a tracking number.",
                    "tool_type": "direct",
                    "input_mode": "resolve",
                    "parameters": {
                        "type": "object",
                        "properties": {"tracking_number": {"type": "string"}},
                        "required": ["tracking_number"],
                    },
                    "handler": get_carrier_contact,
                },
                {
                    "name": "request_eta",
                    "description": "Request delivery ETA from carrier.",
                    "tool_type": "direct",
                    "input_mode": "resolve",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tracking_number": {"type": "string"},
                            "carrier_phone": {"type": "string"},
                            "customer_phone": {"type": "string"},
                        },
                        "required": ["tracking_number", "carrier_phone", "customer_phone"],
                    },
                    "handler": request_eta,
                },
                {
                    "name": "run_carrier_specialist",
                    "description": "Delegate carrier exception handling to a specialist.",
                    "tool_type": "delegated",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tracking_number": {"type": "string"},
                            "customer_phone": {"type": "string"},
                            "customer_email": {"type": "string"},
                        },
                        "required": ["tracking_number", "customer_phone", "customer_email"],
                    },
                    "handler": run_carrier_specialist,
                },
            ],
        )
    )

    result = runtime.invoke(
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
            ],
            "recursion_limit": 8,
        }
    )

    print("Assistant (app-visible):")
    print(result.get("text", ""))
    print("\nSecurity summary:")
    print(result.get("security_summary", {}))


if __name__ == "__main__":
    main()
