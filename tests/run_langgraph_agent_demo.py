import os
import sys
from typing import Any, Annotated, TypedDict

from langgraph.graph import StateGraph, END
try:
    from langgraph.graph import MessagesState, add_messages
except Exception:
    MessagesState = None  # type: ignore
    def add_messages(*args, **kwargs):  # type: ignore
        return None

from dymium.integrations.langgraph import make_tool_node, sanitize_state_messages, deobfuscate_last_message
from dymium.sanitization import Sanitizer
from dymium.redaction import RedactionEngine
from dymium.detectors.pii import PresidioDetector
from langchain.tools import tool


if MessagesState is not None:
    class GraphState(MessagesState, total=False):  # type: ignore[misc,valid-type]
        placeholder_map: dict
        security_summary: dict
        last_sanitized_index: int
else:
    class GraphState(TypedDict, total=False):
        messages: Annotated[list, add_messages]
        placeholder_map: dict
        security_summary: dict
        last_sanitized_index: int


CALLS: list[tuple[str, dict[str, Any]]] = []


@tool
def lookup_customer(email: str) -> dict:
    """Lookup customer by email."""
    CALLS.append(("lookup_customer", {"email": email}))
    return {
        "customer_id": "CUST-1001",
        "phone": "415-555-0135",
        "email": email,
        "alt_email": "alice.alt@example.com",
    }


@tool
def list_recent_orders(customer_id: str) -> dict:
    """List recent orders for a customer."""
    CALLS.append(("list_recent_orders", {"customer_id": customer_id}))
    return {
        "orders": [
            {"order_id": "ORD-9001", "total": "$120.00"},
            {"order_id": "ORD-9000", "total": "$75.00"},
        ]
    }


@tool
def get_order_details(order_id: str) -> dict:
    """Get detailed order information including tracking and ship contact."""
    CALLS.append(("get_order_details", {"order_id": order_id}))
    return {
        "order_id": order_id,
        "tracking_number": "1Z999AA10123456784",
        "warehouse_phone": "415-555-0199",
        "ship_address": "123 Market St, San Francisco, CA",
    }


@tool
def get_carrier_contact(tracking_number: str) -> dict:
    """Get carrier contact info for a tracking number."""
    CALLS.append(("get_carrier_contact", {"tracking_number": tracking_number}))
    return {
        "tracking_number": tracking_number,
        "carrier": "UPS",
        "carrier_phone": "800-555-0100",
    }


@tool
def request_eta(tracking_number: str, carrier_phone: str, customer_phone: str) -> dict:
    """Request ETA from carrier for a tracking number."""
    CALLS.append(("request_eta", {
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
    CALLS.append(("get_shipping_status", {"order_id": order_id, "phone": phone}))
    return {
        "order_id": order_id,
        "status": "in_transit",
        "phone": phone,
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


def main() -> None:
    _require_env("OPENAI_API_KEY")
    presidio_url = os.getenv("PRESIDIO_URL", "http://localhost:5000")
    _require_reachable(presidio_url)

    model = os.getenv("OPENAI_MODEL", "gpt-5")
    if ":" not in model:
        model = f"openai:{model}"

    sanitizer = Sanitizer(
        pii=PresidioDetector(base_url=presidio_url),
        redaction=RedactionEngine(),
    )

    tool_list = [
        lookup_customer,
        list_recent_orders,
        get_order_details,
        get_carrier_contact,
        request_eta,
        get_shipping_status,
    ]

    tools_node = make_tool_node(tool_list, sanitizer)

    from langchain.chat_models import init_chat_model
    lc_model = init_chat_model(model).bind_tools(tool_list)

    def model_node(state: dict) -> dict:
        messages = state["messages"]
        ai_msg = lc_model.invoke(messages)
        return {"messages": [ai_msg]}

    graph = StateGraph(GraphState)
    graph.add_node("model", model_node)
    graph.add_node("tools", tools_node)

    max_tool_calls = 10

    def should_continue(state: dict) -> str:
        messages = state.get("messages", []) or []
        if not messages:
            return END
        last = messages[-1]
        tool_calls = None
        if isinstance(last, dict):
            tool_calls = last.get("tool_calls")
            if tool_calls is None:
                tool_calls = (last.get("additional_kwargs") or {}).get("tool_calls")
        else:
            tool_calls = getattr(last, "tool_calls", None)
            if tool_calls is None:
                tool_calls = getattr(last, "additional_kwargs", {}).get("tool_calls")
        if not tool_calls:
            return END
        tool_usage = (state.get("security_summary") or {}).get("tool_usage", {})
        if tool_usage.get("tool_calls_count", 0) >= max_tool_calls:
            return END
        return "tools"

    graph.add_conditional_edges("model", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "model")
    graph.set_entry_point("model")
    app = graph.compile()

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
                    "6) Check shipping status.\n"
                    "Summarize order ID, status, ETA, tracking number, and confirm which contact phone was used."
                ),
            }
        ]
    }

    inputs = sanitize_state_messages(raw_inputs, sanitizer)

    result = app.invoke(inputs, {"recursion_limit": 25})
    messages = result.get("messages", [])

    deob = deobfuscate_last_message(result, sanitizer)
    print("Assistant (LLM-visible):")
    last = messages[-1]
    content = last.get("content") if isinstance(last, dict) else getattr(last, "content", "")
    print(content)
    print("\nAssistant (deobfuscated):")
    print(deob.get("text_deobfuscated"))
    print("\nSecurity summary:")
    print(result.get("security_summary"))
    print("\nTool calls:")
    print(CALLS)


if __name__ == "__main__":
    main()
