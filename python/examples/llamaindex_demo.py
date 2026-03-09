#!/usr/bin/env python3
"""LlamaIndex + Dymium integration example with richer tool policy flow.

Required env:
- OPENAI_API_KEY

Optional env:
- OPENAI_MODEL (default: gpt-5)
- DYMIUM_PII_MODEL (default: dymium/Dymium-NER-v1)

Dependencies:
- pip install llama-index llama-index-llms-openai
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from llama_index.llms.openai import OpenAI as LlamaOpenAI

from dymium.detectors.pii import HuggingFacePIIDetector
from dymium.integrations.llamaindex import create_sanitized_agent_workflow
from dymium.sanitization import SanitizationContext, Sanitizer


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
    _require_env("OPENAI_API_KEY")
    model_name = os.getenv("OPENAI_MODEL", "gpt-5")
    detector_model = os.getenv("DYMIUM_PII_MODEL", "dymium/Dymium-NER-v1")

    llm = LlamaOpenAI(model=model_name)
    sanitizer = Sanitizer(pii=HuggingFacePIIDetector(model_id=detector_model))
    ctx = SanitizationContext()

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

    workflow = create_sanitized_agent_workflow(
        tools_or_functions=tools,
        llm=llm,
        sanitizer=sanitizer,
        ctx=ctx,
    )

    async def _run() -> Any:
        return await workflow.run(
            (
                "My email is alice@example.com and my phone is 415-555-0135. "
                "Find my account, identify my latest order, retrieve tracking info, "
                "get carrier contact and ETA, and run carrier specialist escalation. "
                "Return order id, status, tracking number, eta, and specialist summary."
            ),
            max_iterations=40,
            early_stopping_method="generate",
        )

    response = asyncio.run(_run())
    text = getattr(getattr(response, "response", None), "content", "") or str(response)

    print("Assistant (app-visible):")
    print(text)
    print("\nSecurity summary:")
    print(ctx.security_summary)


if __name__ == "__main__":
    main()
