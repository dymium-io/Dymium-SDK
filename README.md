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
from langgraph.graph import StateGraph, END
from dymium.integrations.langgraph import make_tool_node, sanitize_state_messages, deobfuscate_last_message
from dymium.sanitization import Sanitizer
from dymium.redaction import RedactionEngine
from dymium.detectors.pii import PresidioDetector
from langchain.chat_models import init_chat_model

sanitizer = Sanitizer(
    pii=PresidioDetector(base_url="https://pii.example.internal"),
    redaction=RedactionEngine(),
)

model = init_chat_model("openai:gpt-5").bind_tools([...])

# tool node
tools_node = make_tool_node([ ... ], sanitizer)

# model node

def model_node(state: dict) -> dict:
    updates = sanitize_state_messages(state, sanitizer)
    messages = updates["messages"]
    ai_msg = model.invoke(messages)
    return {"messages": [ai_msg]}

graph = StateGraph(dict)
graph.add_node("model", model_node)
graph.add_node("tools", tools_node)

def should_continue(state: dict) -> str:
    last = (state.get("messages") or [])[-1]
    tool_calls = last.get("tool_calls") if isinstance(last, dict) else getattr(last, "tool_calls", None)
    return "tools" if tool_calls else END

graph.add_conditional_edges("model", should_continue, {"tools": "tools", END: END})
graph.add_edge("tools", "model")
graph.set_entry_point("model")
app = graph.compile()

inputs = sanitize_state_messages({"messages": [{"role": "user", "content": "Find orders for alice@example.com"}]}, sanitizer)
result = app.invoke(inputs, {"recursion_limit": 12})
print(deobfuscate_last_message(result, sanitizer).get("text_deobfuscated"))
print(result.get("security_summary"))
```

---

## LlamaIndex Integration

```python
from dymium.integrations.llamaindex import SanitizedLLM, wrap_tool_callable
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
safe_llm = SanitizedLLM(llm, sanitizer, ctx=ctx)

# tool wrapper

def lookup_customer(email: str) -> dict:
    return {"customer_id": "CUST-1001", "email": email}

safe_lookup = wrap_tool_callable(lookup_customer, sanitizer, ctx, tool_name="lookup_customer")

response = safe_llm.complete("Email alice@example.com about order 19384")
print(response.text)
print(sanitizer.deobfuscate(response.text, ctx))
```

---

## LLM Providers (SecureRuntime)

- `openai`
- `anthropic`
- `gemini`
- `ghostllm` (Dymium LLM gateway)

Configure with `RuntimeConfig(llm="...", llm_config={...})`.

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
    mcp={"base_url": "http://127.0.0.1:40623/mcp"},
)

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

## Repo Scripts

Demos are in `tests/`:
- `tests/run_langchain_agent_demo.sh`
- `tests/run_langgraph_agent_demo.sh`
- `tests/run_llamaindex_demo.sh`
- `tests/run_secure_runtime_demo.sh`

---

## Context Sheets
- GhostDB chat + redaction + BI agent context: `GhostDB_Context_Sheet.md`
