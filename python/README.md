# Python SDK (Reference)

This is the reference implementation. All other language SDKs must conform to the behavior defined here and in `sdk/spec`.

Package layout:
- `dymium/core/`      Contracts and shared utilities
- `dymium/runtime/`   SecureRuntime orchestration loop
- `dymium/adapters/`  Provider adapters (LLM, PII, MCP)
- `dymium/tools/`     Tool registry and execution boundaries
- `dymium/types/`     Generated types from `sdk/spec`
- `examples/`         Usage stubs and reference flows

Hugging Face local PII detector (optional deps):
- `dymium/adapters/pii/huggingface.py` (`HuggingFacePIIDetector`)
- install extras: `pip install "dymium[hf]"`

## Quick start (factory-based)

```python
from dymium import SecureRuntime, RuntimeConfig

config = RuntimeConfig(
    llm="openai",
    pii="presidio",
    llm_config={"api_key": "..."},
    pii_config={"base_url": "http://localhost:5000"},
    mcp={"base_url": "http://localhost:7000"},
    tool_types={"delegate_to_subagent": "delegated"},  # optional
    tool_direct_input_modes={"web_search": "protect"},  # optional
)

runtime = SecureRuntime.from_config(config)

# Optional: stateful multi-turn session
session = runtime.session()
result = session.run("Hello! My SSN is 123-45-6789.")
```

## Multiple MCP servers

```python
from dymium import SecureRuntime, RuntimeConfig

config = RuntimeConfig(
    llm="openai",
    pii="presidio",
    llm_config={"api_key": "..."},
    pii_config={"base_url": "http://localhost:5000"},
    mcp={
        "servers": [
            {"name": "ghost", "adapter": "mcp", "base_url": "http://ghostmcp.local", "api_key": "..."},
            {"name": "ext", "adapter": "mcp", "base_url": "http://localhost:7001"},
        ],
        "prefix_tools": True,
        "separator": "::",
    },
)

runtime = SecureRuntime.from_config(config)
```

## Multi-turn (session)

```python
from dymium import SecureRuntime, RuntimeConfig

runtime = SecureRuntime.from_config(RuntimeConfig(
    llm="openai",
    pii="presidio",
    llm_config={"api_key": "..."},
    pii_config={"base_url": "http://localhost:5000"},
    mcp={"base_url": "http://localhost:7000"},
))

session = runtime.session()
session.run("My email is me@example.com.")
session.run("Can you summarize what I told you?")
```

## Tool types (direct vs delegated)

Use `tool_types` to define execution boundaries:
- `direct` (default): placeholders are resolved only at tool execution.
- `delegated`: placeholders are passed to the delegated agent/tool runtime through `dymium_context`.

For direct tools, use `tool_direct_input_modes` to control argument handling:
- `resolve` (default): materialize originals at execution time.
- `protect`: keep placeholders in direct tool args.

Delegated handoffs use transport-managed delegation (`delegated_transport` / `DelegatedTransport`).
Delegated context is runtime-managed by Dymium.

For `SecureRuntime`, delegated cross-instance calls can be automatic with `delegated_transport`
on a local delegated tool (no custom handler needed). The runtime forwards
`dymium_context`, including `placeholderMap`.

Remote delegated targets must also run Dymium security (another `SecureRuntime` instance or
an integration path using Dymium sanitizer/middleware) to stay in the same security plane.

For integration-managed tools (LangChain/LangGraph/LlamaIndex), use `DelegatedTransport`
inside delegated tool handlers and pass `dymium_context` to `invoke(...)`.

## Install (local dev)

From `SDK/python`:
```
pip install -e .
```

## Publish (PyPI)

```
python -m build
twine upload dist/*
```
