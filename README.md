# Dymium SDK

Dymium is a security SDK for tool-using LLM apps that keeps sensitive values placeholderized across the model loop and only allows controlled exposure at tool boundaries. Each tool declares a `tool_type`: `direct` for non-agentic execution boundaries like local handlers, DB calls, and constrained APIs, or `delegated` for agentic handoffs to sub-agents or remote secured agents with their own LLM/tool loops. Direct tools also declare `input_mode`, where `resolve` materializes originals only at execution time for trusted operations that need exact values and `protect` keeps placeholders in tool arguments for broader tools where input leakage risk is unacceptable. Delegated tools receive protected inputs plus runtime context so downstream secured runtimes can continue safely, tool outputs are re-sanitized before returning to the LLM, and the app receives deobfuscated output with a security summary.

This repo includes a framework-agnostic **Sanitization module**, an SDK-owned **SecureRuntime** orchestration loop, and integrations for **LangChain**, **LangGraph**, and **LlamaIndex**.

---

## Install

```bash
pip install dymium
```

---

## Supported Detectors (PII)
- `PresidioDetector` (external HTTP service)
- `ComprehendDetector` (AWS)
- `GoogleDLPDetector` (Google Cloud)
- `AzurePIIDetector` (Azure)
- `GhostPIIDetector` (Dymium Detect)
  - Dymium Detect (cloud API)
  - Dymium Detector (local) via `HuggingFacePIIDetector`
- `HuggingFacePIIDetector` (local Transformers; optional deps)
- Optional `regex_rules` (configured with the detector) to supplement the selected detector

Configure detectors via `RuntimeConfig(pii="...", pii_config={...})` or by directly instantiating `Sanitizer`.

---

## Sanitization Module (Fits all Frameworks)

```python
from dymium.sanitization import Sanitizer, SanitizationContext
from dymium.detectors.pii import PresidioDetector
from dymium.redaction import RedactionEngine

sanitizer = Sanitizer(
    pii=PresidioDetector(base_url="https://pii.example.internal"),
    redaction=RedactionEngine(),
)
ctx = SanitizationContext()

safe_text = sanitizer.sanitize_text("Email me at alice@example.com", ctx)
resolved = sanitizer.resolve_for_tool({"email": "PH_EMAIL_ABCDE"}, ctx)
safe_output = sanitizer.sanitize_tool_output({"email": "alice@example.com"}, ctx)
final_text = sanitizer.deobfuscate("Email sent to PH_EMAIL_ABCDE", ctx)
```

---

## Tool Types

Every tool must declare `tool_type`.

`direct` tools are non-agentic tools (local handlers, DB queries, constrained APIs).  
For `direct` tools, `input_mode` is required:
- `resolve`: Dymium resolves placeholders to originals at execution time only.  
  Use this for trusted tools that need original values to function (for example a customer lookup API).
  Common `resolve` cases while keeping the LLM blind to originals:
  - exact-match identity/account lookups (email, phone, account id),
  - order/shipment/ticket retrieval APIs keyed by customer contact fields,
  - parameterized DB queries keyed by sensitive identifiers (email, phone, account id).
- `protect`: Dymium keeps placeholders in args.  
  Use this for broader or less constrained tools where leaking original PII via input is unacceptable
  (for example web/search/send-style tools).

`delegated` tools are agentic handoffs (sub-agent in-process or remote secured agent).  
Dymium does not resolve originals for delegated handoffs. It forwards protected input and runtime context so the
receiving secured agent can continue its own protected tool loop and resolve originals only at its own direct-tool boundary.

This behavior is supported in `SecureRuntime`, `LangChain`, `LangGraph`, and `LlamaIndex`.
- `SecureRuntime`: set policy on each tool definition (`tool_type`, `input_mode` for direct).
- Framework integrations: set policy on each tool object via
  `tool.metadata["dymium"]["tool_type"]` and `tool.metadata["dymium"]["input_mode"]` (required for direct tools).

For `delegated` tools, Dymium passes `dymium_context` with:
- `placeholder_map`
- runtime context metadata (`__dymium_runtime_context`, `__dymium_runtime_context_id`)

Child runtimes can return updated `placeholder_map` and `security_summary` in
`dymium_context`; Dymium merges those back into the parent flow.

Delegated handoffs use transport-managed delegation (`delegated_transport` / `DelegatedTransport`).
Delegated context is runtime-managed by Dymium.

For `SecureRuntime`, remote delegated handoffs can be automatic by defining a local delegated
tool with `delegated_transport` (no custom handler required). Dymium forwards `dymium_context`,
including `placeholderMap`.

To include remote agents in the same security plane, the remote target must also run Dymium
security (for example another `SecureRuntime` instance, or a framework agent wrapped with Dymium
middleware/sanitization). Transport alone is not sufficient if the remote runtime is not secured.

```python
config = RuntimeConfig(
    model="openai:gpt-5",
    pii="presidio",
    model_config={"api_key": "..."},
    pii_config={"base_url": "http://localhost:5000"},
    tools=[
        {
            "name": "run_specialist",
            "description": "Delegate to a remote Dymium SecureRuntime instance.",
            "parameters": {
                "type": "object",
                "properties": {"handoff_request": {"type": "string"}},
                "required": ["handoff_request"],
            },
            "tool_type": "delegated",
            "delegated_transport": {
                "kind": "http",
                "url": "http://specialist-agent:8080/invoke",
                "prompt_arg": "handoff_request",
                "timeout_s": 30,
            },
        }
    ],
)
```

For framework integrations, use `DelegatedTransport` inside delegated tools so remote handoffs
are automatic without manual payload plumbing:

```python
from dymium import DelegatedTransport

remote = DelegatedTransport(
    {"kind": "http", "url": "http://specialist-agent:8080/invoke"},
    name="run_specialist",
)

def run_specialist(handoff_request: str, customer_email: str, dymium_context: dict | None = None) -> dict:
    return remote.invoke(
        {"handoff_request": handoff_request, "customer_email": customer_email},
        dymium_context=dymium_context,
    )
```

---

## LangChain Integration

```python
from dymium.integrations.langchain import DymiumMiddleware
from dymium.sanitization import Sanitizer
from dymium.redaction import RedactionEngine
from dymium.detectors.pii import PresidioDetector
from langchain.agents import create_agent

sanitizer = Sanitizer(
    pii=PresidioDetector(base_url="https://pii.example.internal"),
    redaction=RedactionEngine(),
)

tools = [...]  # Define tool objects first.

for t in tools:
    meta = dict(getattr(t, "metadata", {}) or {})
    meta["dymium"] = {
        "tool_type": "direct",
        "input_mode": "resolve",
    }
    t.metadata = meta

middleware = DymiumMiddleware(sanitizer, tools=tools).middleware()

agent = create_agent(
    model="openai:gpt-5",
    tools=tools,
    middleware=[middleware],
)

result = agent.invoke({"messages": [{"role": "user", "content": "Find orders for alice@example.com"}]})
messages = result.get("messages", [])
last_text = messages[-1].get("content", "") if messages else result.get("text", "")
print(last_text)
print(result.get("security_summary"))
```

`result["messages"]` is app-visible and deobfuscated.

---

## LangGraph Integration

```python
from dymium.integrations.langgraph import (
    create_sanitized_agent,
    DymiumMessagesState,
)
from dymium.sanitization import Sanitizer
from dymium.redaction import RedactionEngine
from dymium.detectors.pii import PresidioDetector
from langchain.chat_models import init_chat_model

sanitizer = Sanitizer(
    pii=PresidioDetector(base_url="https://pii.example.internal"),
    redaction=RedactionEngine(),
)

model = init_chat_model("openai:gpt-5")
tools = [...]

for t in tools:
    meta = dict(getattr(t, "metadata", {}) or {})
    meta["dymium"] = {
        "tool_type": "direct",
        "input_mode": "resolve",
    }
    t.metadata = meta

app = create_sanitized_agent(
    model=model,
    tools=tools,
    sanitizer=sanitizer,
    state_schema=DymiumMessagesState,
    max_tool_calls=10,
)

result = app.invoke(
    {"messages": [{"role": "user", "content": "Find orders for alice@example.com"}]},
    {"recursion_limit": 12},
)
messages = result.get("messages", [])
last_text = messages[-1].get("content", "") if messages else ""
print(last_text)
print(result.get("security_summary"))
```

---

## LlamaIndex Integration

```python
import asyncio

from dymium.integrations.llamaindex import create_sanitized_agent_workflow
from dymium.sanitization import Sanitizer, SanitizationContext
from dymium.detectors.pii import PresidioDetector
from dymium.redaction import RedactionEngine
from llama_index.llms.openai import OpenAI as LlamaOpenAI

sanitizer = Sanitizer(
    pii=PresidioDetector(base_url="https://pii.example.internal"),
    redaction=RedactionEngine(),
)
ctx = SanitizationContext()

llm = LlamaOpenAI(model="gpt-5")

def lookup_customer(email: str) -> dict:
    return {"customer_id": "CUST-1001", "email": email}

lookup_customer.metadata = {
    "dymium": {
        "tool_type": "direct",
        "input_mode": "resolve",
    }
}

workflow = create_sanitized_agent_workflow(
    tools_or_functions=[lookup_customer],
    llm=llm,
    sanitizer=sanitizer,
    ctx=ctx,
)

async def _run():
    return await workflow.run(user_msg="Find customer details for alice@example.com")

result = asyncio.run(_run())
text = getattr(getattr(result, "response", None), "content", "") or str(result)
print(text)
print(ctx.security_summary)
```

The returned LlamaIndex response object is app-visible and deobfuscated.

---

## LLM Providers (SecureRuntime)

- `openai`
- `anthropic`
- `gemini`
- `ghostllm` (Dymium LLM gateway)

Preferred config style: `RuntimeConfig(model="provider:model", model_config={...})`.
Legacy style also works: `RuntimeConfig(llm="provider", llm_config={"model": "...", ...})`.

Framework integrations (LangChain/LangGraph/LlamaIndex) use the framework’s own LLM objects; Dymium supplies the sanitization boundary and tool wrapping.

---

## SecureRuntime (SDK‑Owned Loop)

```python
from dymium import RuntimeConfig, SecureRuntime

def lookup_customer(email: str) -> dict:
    "Lookup customer by email."
    return {"customer_id": "CUST-1001", "email": email}

config = RuntimeConfig(
    model="openai:gpt-5",
    pii="presidio",
    model_config={"api_key": "..."},
    pii_config={
        "base_url": "https://pii.example.internal",
        "regex_rules": [{"pattern": "ORD-\\d+", "type": "ORDER_ID"}],
    },
    tools=[
        {
            "name": "lookup_customer",
            "description": "Lookup customer by email.",
            "parameters": {
                "type": "object",
                "properties": {"email": {"type": "string"}},
                "required": ["email"],
            },
            "tool_type": "direct",
            "input_mode": "resolve",
            "handler": lookup_customer,
        }
    ],
)

# Optional: add MCP alongside local tools.
# config.mcp = {"base_url": "http://127.0.0.1:40623/mcp"}

runtime = SecureRuntime.from_config(config)

request = {
    "messages": [
        {"role": "user", "content": "Find orders for alice@example.com"}
    ],
    "recursion_limit": 5,
}

result = runtime.invoke(request)
messages = result.get("messages", [])
last_text = messages[-1].get("content", "") if messages else result.get("text", "")
print(last_text)
print(result.get("security_summary"))
```

`result["messages"]` is app-visible and deobfuscated.

---

## Detector Configuration (Sanitization Module / Integrations)

Use detector instances directly in `Sanitizer(...)` for framework integrations and custom loops.
For brevity, non-Presidio snippets reuse `Sanitizer` and `RedactionEngine` imports from the Presidio example.

### Presidio

```python
from dymium.sanitization import Sanitizer
from dymium.redaction import RedactionEngine
from dymium.detectors.pii import PresidioDetector

sanitizer = Sanitizer(
    pii=PresidioDetector(
        base_url="https://pii.example.internal",
        timeout_s=10,
        regex_rules=[{"pattern": "ORD-\\d+", "type": "ORDER_ID"}],
    ),
    redaction=RedactionEngine(),
)
```

### GhostPII (Dymium Detect)

```python
from dymium.detectors.pii import GhostPIIDetector

sanitizer = Sanitizer(
    pii=GhostPIIDetector(
        # either service root (SDK appends /v1/detect/pii)...
        base_url="https://spoofcorp.llm.dymium.home:3000",
        # ...or full endpoint URL ending in /v1/detect/pii
        api_key="...",  # required
        timeout_s=10,
        entity_types=["ID_REF", "EMAIL", "URL"],  # optional allow-list; defaults to all 13
        regex_rules=[{"pattern": "ORD-\\d+", "type": "ORDER_ID"}],
    ),
    redaction=RedactionEngine(),
)
```

### AWS Comprehend

```python
from dymium.detectors.pii import ComprehendDetector

sanitizer = Sanitizer(
    pii=ComprehendDetector(
        region="us-east-1",
        credentials={
            "access_key_id": "...",
            "secret_access_key": "...",
            "session_token": "...",  # optional
        },
        endpoint_url=None,  # optional custom endpoint
        regex_rules=[{"pattern": "ORD-\\d+", "type": "ORDER_ID"}],
    ),
    redaction=RedactionEngine(),
)
```

### Google Cloud DLP

```python
from dymium.detectors.pii import GoogleDLPDetector

sanitizer = Sanitizer(
    pii=GoogleDLPDetector(
        project_id="my-gcp-project",
        location_id="us",  # optional regional parent
        credentials={
            "token": "...",  # or api_key
            # "api_key": "...",
        },
        base_url="https://dlp.googleapis.com",
        timeout_s=10,
        regex_rules=[{"pattern": "ORD-\\d+", "type": "ORDER_ID"}],
    ),
    redaction=RedactionEngine(),
)
```

### Azure PII

```python
from dymium.detectors.pii import AzurePIIDetector

sanitizer = Sanitizer(
    pii=AzurePIIDetector(
        endpoint="https://my-language-resource.cognitiveservices.azure.com",
        api_key="...",  # or bearer_token
        bearer_token=None,  # optional
        api_version="2022-05-01",
        use_legacy_endpoint=False,
        timeout_s=10,
        regex_rules=[{"pattern": "ORD-\\d+", "type": "ORDER_ID"}],
    ),
    redaction=RedactionEngine(),
)
```

### Hugging Face (local Transformers)

Install optional local inference dependencies:

```bash
pip install "dymium[hf]"
```

Use the built-in detector directly with `Sanitizer`:

```python
from dymium.detectors.pii import HuggingFacePIIDetector
from dymium.sanitization import Sanitizer
from dymium.redaction import RedactionEngine

sanitizer = Sanitizer(
    pii=HuggingFacePIIDetector(
        model_id="dymium/Dymium-NER-v1",
        aggregation_strategy="simple",
        score_threshold=0.5,
        # device=0,  # optional GPU index
        regex_rules=[{"pattern": "ORD-\\d+", "type": "ORDER_ID"}],
    ),
    redaction=RedactionEngine(),
)
```

Use with `SecureRuntime.from_config(...)`:

```python
from dymium import RuntimeConfig, SecureRuntime

runtime = SecureRuntime.from_config(
    RuntimeConfig(
        model="openai:gpt-5",
        pii="huggingface",  # alias: "dymium_hf"
        model_config={"api_key": "..."},
        pii_config={
            "model_id": "dymium/Dymium-NER-v1",
            "score_threshold": 0.5,
        },
        tools=[...],
    )
)
```

Detection behavior should be configured when creating the detector instance (for example language defaults, provider filters, and thresholds).
