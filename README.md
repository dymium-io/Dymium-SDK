# Dymium SDK

Dymium is a security SDK for tool‑using LLM apps. It enforces a strict boundary:
- The LLM only sees placeholderized sensitive values.
- Tool type controls the boundary:
  - `direct`: placeholders are resolved at execution time by default (configurable per tool).
  - `delegated`: placeholders are passed through to the delegated agent/tool runtime.
- Tool outputs are re‑sanitized before the LLM sees them.
- The app/caller receives deobfuscated output plus a security summary.

This repo includes:
- A **Sanitization module** (framework‑agnostic).
- A **SecureRuntime** (SDK‑owned orchestration loop).
- Framework integrations for **LangChain**, **LangGraph**, and **LlamaIndex**.

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

## Tool Types (Optional)

`tool_type` controls placeholder handling at tool boundaries:
- `direct` (default): resolve placeholders before the tool call.
- `delegated`: pass placeholders through unchanged and pass runtime context to the delegated agent/tool. This is intended for sub agents in the main agent runtime or for dymium secured agents running in separate instances altogether.

For direct tools, optional `direct_input_mode` adds a per-tool knob:
- `resolve` (default): materialize originals at execution time.
- `protect`: keep placeholders in direct tool args.

This behavior is supported in `SecureRuntime`, `LangChain`, `LangGraph`, and `LlamaIndex` integrations.

For `delegated` tools, Dymium passes `dymium_context` with:
- `placeholder_map`
- runtime context metadata (`__dymium_runtime_context`, `__dymium_runtime_context_id`)

Child runtimes can return updated `placeholder_map` and `security_summary` in
`dymium_context`; Dymium merges those back into the parent flow.

Single-path design for delegated handoffs:
- Use transport-managed delegation only (`delegated_transport` / `DelegatedTransport`).
- Do not manually plumb `placeholder_map` / `security_summary` in app tool code.
- Delegated context must be runtime-managed by Dymium (tool-supplied/manual contexts are rejected).

For `SecureRuntime`, remote delegated handoffs can be automatic by defining a local delegated
tool with `delegated_transport` (no custom handler required). Dymium forwards `dymium_context`,
passes `placeholderMap`, and merges returned child context updates.

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

middleware = DymiumMiddleware(
    sanitizer,
    tool_types={"delegate_to_subagent": "delegated"},  # optional
    tool_direct_input_modes={"web_search": "protect"},  # optional
).middleware()

agent = create_agent(
    model="openai:gpt-5",
    tools=[...],
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

app = create_sanitized_agent(
    model=model,
    tools=[...],
    sanitizer=sanitizer,
    state_schema=DymiumMessagesState,
    max_tool_calls=10,
    tool_types={"delegate_to_subagent": "delegated"},  # optional
    tool_direct_input_modes={"web_search": "protect"},  # optional
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

workflow = create_sanitized_agent_workflow(
    tools_or_functions=[lookup_customer],
    llm=llm,
    sanitizer=sanitizer,
    ctx=ctx,
    tool_types={"delegate_to_subagent": "delegated"},  # optional
    tool_direct_input_modes={"web_search": "protect"},  # optional
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
            "direct_input_mode": "resolve",
            "handler": lookup_customer,
        }
    ],
    tool_types={"delegate_to_subagent": "delegated"},  # optional per-tool override
    tool_direct_input_modes={"web_search": "protect"},  # optional direct-tool override
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
