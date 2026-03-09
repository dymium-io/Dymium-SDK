#!/usr/bin/env python3
"""SecureRuntime example with richer tool policy flow.

Required env:
- OPENAI_API_KEY

Optional env:
- OPENAI_MODEL (default: gpt-5)
- DYMIUM_PII_MODEL (default: dymium/Dymium-NER-v1)
"""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import date, timedelta
from typing import Any

from dymium import RuntimeConfig, SecureRuntime


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    print(f"Missing env var: {name}", file=sys.stderr)
    sys.exit(1)


_STATUS_FLOW = ("processing", "label_created", "in_transit", "out_for_delivery")
_CARRIERS = (
    ("UPS", "800-555-0100"),
    ("FedEx", "800-555-0101"),
    ("USPS", "800-555-0102"),
)


def _stable_number(seed: str, modulus: int) -> int:
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % modulus


def _order_id_for_customer(customer_id: str) -> str:
    return f"ORD-{1000 + _stable_number(customer_id, 8000)}"


def _tracking_for_order(order_id: str) -> str:
    return f"1Z{10_000_000 + _stable_number(order_id, 90_000_000)}AA101234"


def lookup_customer(email: str) -> dict[str, Any]:
    normalized = email.strip().lower()
    plan = ("starter", "growth", "enterprise")[_stable_number(normalized, 3)]
    customer_id = f"CUST-{1000 + _stable_number(normalized, 9000)}"
    phone = f"415-555-{1000 + _stable_number(normalized + '-phone', 9000):04d}"
    return {
        "customer_id": customer_id,
        "email": normalized,
        "phone": phone,
        "plan": plan,
    }


def list_recent_orders(customer_id: str) -> dict[str, Any]:
    latest = _order_id_for_customer(customer_id)
    prior_num = max(int(latest.split("-")[1]) - 1, 1)
    prior = f"ORD-{prior_num}"
    return {
        "customer_id": customer_id,
        "orders": [
            {
                "order_id": latest,
                "total": f"${79 + _stable_number(latest, 140)}.00",
            },
            {
                "order_id": prior,
                "total": f"${49 + _stable_number(prior, 120)}.00",
            },
        ],
    }


def get_order_details(order_id: str) -> dict[str, Any]:
    streets = ("Market", "Mission", "Howard", "Folsom")
    cities = ("San Francisco", "Oakland", "San Jose")
    street = streets[_stable_number(order_id + "-street", len(streets))]
    city = cities[_stable_number(order_id + "-city", len(cities))]
    return {
        "order_id": order_id,
        "tracking_number": _tracking_for_order(order_id),
        "ship_address": f"{100 + _stable_number(order_id, 900)} {street} St, {city}, CA",
    }


def get_shipping_status(order_id: str, phone: str) -> dict[str, Any]:
    status = _STATUS_FLOW[_stable_number(order_id + phone, len(_STATUS_FLOW))]
    events = ("sorted at facility", "departed hub", "arrived at local center", "out for final delivery")
    return {
        "order_id": order_id,
        "status": status,
        "last_event": events[_stable_number(order_id + "-event", len(events))],
        "contact_phone_last4": phone[-4:] if len(phone) >= 4 else phone,
    }


def get_carrier_contact(tracking_number: str) -> dict[str, Any]:
    carrier, carrier_phone = _CARRIERS[_stable_number(tracking_number, len(_CARRIERS))]
    return {
        "tracking_number": tracking_number,
        "carrier": carrier,
        "carrier_phone": carrier_phone,
        "support_case_prefix": f"{carrier[:2].upper()}-{_stable_number(tracking_number + '-case', 900)}",
    }


def request_eta(tracking_number: str, carrier_phone: str, customer_phone: str) -> dict[str, Any]:
    eta = date.today() + timedelta(days=1 + _stable_number(tracking_number + carrier_phone, 4))
    windows = ("9am-12pm", "12pm-4pm", "2pm-6pm", "6pm-9pm")
    return {
        "tracking_number": tracking_number,
        "carrier_phone": carrier_phone,
        "eta": eta.isoformat(),
        "eta_window": windows[_stable_number(customer_phone + tracking_number, len(windows))],
        "contact_phone_last4": customer_phone[-4:] if len(customer_phone) >= 4 else customer_phone,
    }


def lookup_carrier_sla(tracking_number: str) -> dict[str, Any]:
    carrier, carrier_phone = _CARRIERS[_stable_number(tracking_number, len(_CARRIERS))]
    promised = ("Tomorrow 9am-12pm", "Tomorrow 12pm-4pm", "Tomorrow 2pm-6pm")
    return {
        "tracking_number": tracking_number,
        "carrier": carrier,
        "carrier_phone": carrier_phone,
        "promised_window": promised[_stable_number(tracking_number + "-window", len(promised))],
    }


def open_carrier_callback(carrier_phone: str, customer_email: str) -> dict[str, Any]:
    ticket_seed = f"{carrier_phone}:{customer_email.strip().lower()}"
    return {
        "carrier_phone": carrier_phone,
        "ticket_id": f"TCK-{1000 + _stable_number(ticket_seed, 9000)}",
        "queue": "delivery-exceptions",
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
