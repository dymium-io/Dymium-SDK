# Dymium SDK

Dymium is a security SDK for tool‑using LLM apps. It enforces a strict boundary:
- The LLM only sees placeholderized sensitive values.
- Placeholders are resolved only at tool execution.
- Tool outputs are re‑sanitized before the LLM sees them.
- The caller receives a deobfuscated final answer plus a security summary.

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
  - Dymium Detect (cloud) — **TBD**
  - Dymium Detector (local) — **TBD**
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

middleware = DymiumMiddleware(sanitizer).middleware()

agent = create_agent(
    model="openai:gpt-5",
    tools=[...],
    middleware=[middleware],
)

result = agent.invoke({"messages": [{"role": "user", "content": "Find orders for alice@example.com"}]})
print(result.get("text_deobfuscated"))
print(result.get("security_summary"))
```

---

## LangGraph Integration

```python
from dymium.integrations.langgraph import (
    create_sanitized_agent,
    DymiumMessagesState,
    deobfuscate_last_message,
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
)

result = app.invoke(
    {"messages": [{"role": "user", "content": "Find orders for alice@example.com"}]},
    {"recursion_limit": 12},
)
print(deobfuscate_last_message(result, sanitizer).get("text_deobfuscated"))
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
)

async def _run():
    return await workflow.run(user_msg="Find customer details for alice@example.com")

result = asyncio.run(_run())
text = getattr(getattr(result, "response", None), "content", "") or str(result)
print(text)
print(sanitizer.deobfuscate(text, ctx))
print(ctx.security_summary)
```

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
print(result.get("text_deobfuscated"))
print(result.get("security_summary"))
```

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
        base_url="https://detect.example.internal",
        api_key="...",  # optional
        timeout_s=10,
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

Optional per-call detection options can be passed through `pii_options` when calling sanitizer methods (for example language, provider filters, thresholds).
