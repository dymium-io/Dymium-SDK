import os
import sys
from typing import Any

from dymium.integrations.llamaindex import SanitizedLLM, wrap_tool_callable
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


def main() -> None:
    _require_env("OPENAI_API_KEY")
    presidio_url = os.getenv("PRESIDIO_URL", "http://localhost:5000")
    _require_reachable(presidio_url)

    try:
        from llama_index.core.llms import LLM
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
    safe_llm = SanitizedLLM(llm, sanitizer, ctx=ctx)

    def lookup_customer(email: str) -> dict:
        return {"customer_id": "CUST-1001", "email": email}

    safe_lookup = wrap_tool_callable(lookup_customer, sanitizer, ctx, tool_name="lookup_customer")

    # Example prompt with PII
    prompt = "Email alice@example.com about order 19384 and confirm shipment status."
    response = safe_llm.complete(prompt)
    print("LLM response (placeholder-safe):")
    print(response.text)

    tool_result = safe_lookup(email="alice@example.com")
    print("Tool result (sanitized):")
    print(tool_result)

    deobfuscated = sanitizer.deobfuscate(response.text, ctx)
    print("Deobfuscated example:")
    print(deobfuscated)


if __name__ == "__main__":
    main()
