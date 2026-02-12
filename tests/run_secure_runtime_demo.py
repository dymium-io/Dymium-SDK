import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from dymium import RuntimeConfig, SecureRuntime

CALLS: list[tuple[str, dict[str, Any]]] = []
OUTPUTS: list[tuple[str, dict[str, Any]]] = []
PLACEHOLDER_RE = re.compile(r"PH_[A-Z]+_[A-Z0-9]{5}")


TOOLS = [
    {
        "name": "lookup_customer",
        "description": "Lookup customer by email.",
        "inputSchema": {
            "type": "object",
            "properties": {"email": {"type": "string"}},
            "required": ["email"],
        },
    },
    {
        "name": "list_recent_orders",
        "description": "List recent orders for a customer.",
        "inputSchema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "get_order_details",
        "description": "Get detailed order information including tracking and ship contact.",
        "inputSchema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "get_shipping_status",
        "description": "Get shipping status for an order and phone on file.",
        "inputSchema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}, "phone": {"type": "string"}},
            "required": ["order_id", "phone"],
        },
    },
    {
        "name": "get_carrier_contact",
        "description": "Get carrier contact info for a tracking number.",
        "inputSchema": {
            "type": "object",
            "properties": {"tracking_number": {"type": "string"}},
            "required": ["tracking_number"],
        },
    },
    {
        "name": "request_eta",
        "description": "Request ETA from carrier for a tracking number.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tracking_number": {"type": "string"},
                "carrier_phone": {"type": "string"},
                "customer_phone": {"type": "string"},
            },
            "required": ["tracking_number", "carrier_phone", "customer_phone"],
        },
    },
]


def _tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "json", "json": payload}], "isError": False}


def lookup_customer(email: str) -> dict[str, Any]:
    CALLS.append(("lookup_customer", {"email": email}))
    output = {
        "customer_id": "CUST-1001",
        "phone": "415-555-0135",
        "email": email,
        "alt_email": "alice.alt@example.com",
    }
    OUTPUTS.append(("lookup_customer", output))
    return output


def list_recent_orders(customer_id: str) -> dict[str, Any]:
    CALLS.append(("list_recent_orders", {"customer_id": customer_id}))
    output = {
        "orders": [
            {"order_id": "ORD-9001", "total": "$120.00"},
            {"order_id": "ORD-9000", "total": "$75.00"},
        ]
    }
    OUTPUTS.append(("list_recent_orders", output))
    return output


def get_order_details(order_id: str) -> dict[str, Any]:
    CALLS.append(("get_order_details", {"order_id": order_id}))
    output = {
        "order_id": order_id,
        "tracking_number": "1Z999AA10123456784",
        "warehouse_phone": "415-555-0199",
        "ship_address": "123 Market St, San Francisco, CA",
    }
    OUTPUTS.append(("get_order_details", output))
    return output


def get_shipping_status(order_id: str, phone: str) -> dict[str, Any]:
    CALLS.append(("get_shipping_status", {"order_id": order_id, "phone": phone}))
    output = {"order_id": order_id, "status": "in_transit", "phone": phone}
    OUTPUTS.append(("get_shipping_status", output))
    return output


def get_carrier_contact(tracking_number: str) -> dict[str, Any]:
    CALLS.append(("get_carrier_contact", {"tracking_number": tracking_number}))
    output = {"tracking_number": tracking_number, "carrier": "UPS", "carrier_phone": "800-555-0100"}
    OUTPUTS.append(("get_carrier_contact", output))
    return output


def request_eta(tracking_number: str, carrier_phone: str, customer_phone: str) -> dict[str, Any]:
    CALLS.append(("request_eta", {
        "tracking_number": tracking_number,
        "carrier_phone": carrier_phone,
        "customer_phone": customer_phone,
    }))
    output = {"tracking_number": tracking_number, "eta": "2026-02-15", "carrier_phone": carrier_phone}
    OUTPUTS.append(("request_eta", output))
    return output


TOOL_FUNCS = {
    "lookup_customer": lookup_customer,
    "list_recent_orders": list_recent_orders,
    "get_order_details": get_order_details,
    "get_shipping_status": get_shipping_status,
    "get_carrier_contact": get_carrier_contact,
    "request_eta": request_eta,
}


class MCPHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003 - match BaseHTTPRequestHandler
        return

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._send_json({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            return

        method = payload.get("method")
        request_id = payload.get("id")
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "dymium-test-mcp", "version": "0.1"},
            }
            self._send_json({"jsonrpc": "2.0", "id": request_id, "result": result})
            return

        if method == "tools/list":
            self._send_json({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
            return

        if method == "tools/call":
            params = payload.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name not in TOOL_FUNCS:
                self._send_json({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"unknown tool: {name}"},
                })
                return
            try:
                result_payload = TOOL_FUNCS[name](**arguments)
            except TypeError as exc:
                self._send_json({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": f"invalid params: {exc}"},
                })
                return
            self._send_json({"jsonrpc": "2.0", "id": request_id, "result": _tool_result(result_payload)})
            return

        self._send_json({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"unknown method: {method}"},
        })

    def _send_json(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class MCPServer:
    def __init__(self) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.base_url: str | None = None

    def start(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), MCPHandler)
        host, port = self._server.server_address
        self.base_url = f"http://{host}:{port}/mcp"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._server:
            return
        self._server.shutdown()
        if self._thread:
            self._thread.join(timeout=5)


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

    server = MCPServer()
    server.start()
    try:
        config = RuntimeConfig(
            model="openai:gpt-5",
            pii="presidio",
            llm_config={"api_key": os.getenv("OPENAI_API_KEY"), "model": model},
            pii_config={"base_url": presidio_url},
            mcp={"base_url": server.base_url},
        )
        runtime = SecureRuntime.from_config(config)

        request = {
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
            ],
            "recursion_limit": 8,
        }

        result = runtime.invoke(request)

        print("Assistant (LLM-visible):")
        print(result.get("text", ""))
        print("\nAssistant (deobfuscated):")
        print(result.get("text_deobfuscated", ""))
        print("\nSecurity summary:")
        print(result.get("security_summary"))
        print("\nTool calls:")
        print(CALLS)

        if not CALLS:
            print("\nFAIL: No tools were called. Ensure your model supports tool calling.", file=sys.stderr)
            sys.exit(1)

        expected_tools = {
            "lookup_customer",
            "list_recent_orders",
            "get_order_details",
            "get_carrier_contact",
            "request_eta",
            "get_shipping_status",
        }
        seen_tools = {name for name, _ in CALLS}
        missing = expected_tools - seen_tools
        failures = 0
        if missing:
            print(f"FAIL: Missing tool calls: {sorted(missing)}", file=sys.stderr)
            failures += 1

        for name, args in CALLS:
            for key, value in args.items():
                if isinstance(value, str) and PLACEHOLDER_RE.search(value):
                    print(f"FAIL: Placeholder leaked into tool arg for {name}.{key}: {value}", file=sys.stderr)
                    failures += 1

        req_eta = next((a for n, a in CALLS if n == "request_eta"), None)
        if not req_eta:
            print("FAIL: request_eta not called.", file=sys.stderr)
            failures += 1
        elif req_eta.get("carrier_phone") != "800-555-0100":
            print("FAIL: carrier_phone was not resolved for request_eta.", file=sys.stderr)
            failures += 1
        else:
            print("PASS: carrier_phone resolved for request_eta.")

        if req_eta and req_eta.get("customer_phone") != "415-555-0135":
            print("FAIL: customer_phone was not resolved for request_eta.", file=sys.stderr)
            failures += 1

        llm_visible = result.get("text", "") or ""
        deobfuscated = result.get("text_deobfuscated", "") or ""
        if PLACEHOLDER_RE.search(llm_visible) and PLACEHOLDER_RE.search(deobfuscated):
            print("FAIL: placeholders were not deobfuscated in final response.", file=sys.stderr)
            failures += 1
        elif not PLACEHOLDER_RE.search(llm_visible):
            print("WARN: LLM-visible response contained no placeholders.")

        if failures:
            sys.exit(1)
    finally:
        server.stop()


if __name__ == "__main__":
    main()
