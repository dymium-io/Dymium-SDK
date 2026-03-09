# Dymium SDK

Dymium is a security SDK for tool-using LLM apps. It keeps sensitive values placeholderized across the model loop and only allows controlled exposure at tool boundaries.

Detection happens in the sanitizer layer using your configured PII detector (for example Hugging Face, Comprehend, Google DLP, Azure, or GhostPII). Sensitive values detected in user input and tool output are replaced with placeholders and tracked in runtime context, so agents can still reason over requests and drive tool workflows that depend on sensitive fields without ever seeing the raw values.

Each tool declares how that protected context is handled. `tool_type="direct"` is for non-agentic execution boundaries (local handlers, DB/API calls, deterministic services) and requires `input_mode`: use `resolve` when the trusted tool must receive originals at execution time, or `protect` when placeholders must remain in tool args. `tool_type="delegated"` is for agentic handoffs (sub-agents or remote secured runtimes): Dymium forwards protected input plus runtime context so the downstream secured runtime can continue safely. Tool outputs are re-sanitized before returning to the model, and the app receives deobfuscated output with a security summary.

This repo includes a framework-agnostic **Sanitization module**, an SDK-owned **SecureRuntime** orchestration loop, and integrations for **LangChain**, **LangGraph**, and **LlamaIndex**.

---

## Install

```bash
pip install dymium
```

---

## Supported Detectors (PII)
- `HuggingFacePIIDetector` (local Transformers; Dymium flagship model)
- `ComprehendDetector` (AWS)
- `GoogleDLPDetector` (Google Cloud)
- `AzurePIIDetector` (Azure)
- `GhostPIIDetector` (Dymium Detect)
  - Dymium Detect (cloud API)
  - Dymium Detector (local) via `HuggingFacePIIDetector`
- Optional `regex_rules` (configured with the detector) to supplement the selected detector

Configure detectors via `RuntimeConfig(pii="...", pii_config={...})` or by directly instantiating `Sanitizer`.

---

## Sanitization Module (Fits all Frameworks)

```python
from dymium.sanitization import Sanitizer, SanitizationContext
from dymium.detectors.pii import HuggingFacePIIDetector

sanitizer = Sanitizer(
    pii=HuggingFacePIIDetector(model_id="dymium/Dymium-NER-v1"),
)
ctx = SanitizationContext()

safe_text = sanitizer.sanitize_text("Email me at alice@example.com", ctx)
resolved = sanitizer.resolve_for_tool({"email": "PH_EMAIL_ABCDE"}, ctx)
safe_output = sanitizer.sanitize_tool_output({"email": "alice@example.com"}, ctx)
final_text = sanitizer.deobfuscate("Email sent to PH_EMAIL_ABCDE", ctx)
```

`Sanitizer` uses the built-in `RedactionEngine` by default; pass a custom redaction engine only when you need custom placeholder behavior.

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
Unlike `direct`, a delegated handoff crosses into another LLM/tool loop outside the parent loop, so resolving originals at
the parent boundary would break the security plane. Dymium therefore keeps inputs protected and forwards runtime context so
the receiving secured agent can continue safely and resolve originals only at its own `direct` tool boundary.

This behavior is supported in `SecureRuntime`, `LangChain`, `LangGraph`, and `LlamaIndex`.
- `SecureRuntime`: set policy on each tool definition (`tool_type`, `input_mode` for direct).
- Framework integrations: set policy on each tool object via
  `tool.metadata["dymium"]["tool_type"]` and `tool.metadata["dymium"]["input_mode"]` (required for direct tools).

For `delegated` tools, Dymium manages `dymium_context` internally with:
- `placeholder_map`
- runtime context metadata (`__dymium_runtime_context`, `__dymium_runtime_context_id`)

Child runtimes return updated `placeholder_map` and `security_summary` in
response `dymium_context`; Dymium validates and merges those back into the parent flow.

Delegated handoffs use transport-managed delegation (`delegated_transport` / `DelegatedTransport`).
Delegated context is runtime-managed by Dymium.

For `SecureRuntime`, remote delegated handoffs can be automatic by defining a local delegated
tool with `delegated_transport` (no custom handler required). Dymium forwards runtime context,
including `placeholderMap`.

To include remote agents in the same security plane, the remote target must also run Dymium
security (for example another `SecureRuntime` instance, or a framework agent wrapped with Dymium
middleware/sanitization). Transport alone is not sufficient if the remote runtime is not secured.

```python
config = RuntimeConfig(
    model="openai:gpt-5",
    pii="dymium_hf",
    model_config={"api_key": "..."},
    pii_config={"model_id": "dymium/Dymium-NER-v1"},
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

# Tool args stay business-only; context injection is runtime-owned.
run_specialist = remote.as_tool_handler()
```

---

## LangChain Integration

```python
from dymium.integrations.langchain import DymiumMiddleware
from dymium.sanitization import Sanitizer
from dymium.detectors.pii import HuggingFacePIIDetector
from langchain.agents import create_agent

sanitizer = Sanitizer(
    pii=HuggingFacePIIDetector(model_id="dymium/Dymium-NER-v1"),
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
from dymium.detectors.pii import HuggingFacePIIDetector
from langchain.chat_models import init_chat_model

sanitizer = Sanitizer(
    pii=HuggingFacePIIDetector(model_id="dymium/Dymium-NER-v1"),
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
from dymium.detectors.pii import HuggingFacePIIDetector
from llama_index.llms.openai import OpenAI as LlamaOpenAI

sanitizer = Sanitizer(
    pii=HuggingFacePIIDetector(model_id="dymium/Dymium-NER-v1"),
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
    pii="dymium_hf",
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
For brevity, non-Hugging Face snippets reuse `Sanitizer` imports from the Hugging Face example.

### Hugging Face (Dymium flagship)

```python
from dymium.sanitization import Sanitizer
from dymium.detectors.pii import HuggingFacePIIDetector

sanitizer = Sanitizer(
    pii=HuggingFacePIIDetector(
        model_id="dymium/Dymium-NER-v1",
        score_threshold=0.5,
        regex_rules=[{"pattern": "ORD-\\d+", "type": "ORDER_ID"}],
    ),
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

sanitizer = Sanitizer(
    pii=HuggingFacePIIDetector(
        model_id="dymium/Dymium-NER-v1",
        score_threshold=0.5,
        # device=0,  # optional GPU index
        regex_rules=[{"pattern": "ORD-\\d+", "type": "ORDER_ID"}],
    ),
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
