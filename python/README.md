# Python SDK (Reference)

This is the reference implementation. All other language SDKs must conform to the behavior defined here and in `sdk/spec`.

Package layout:
- `dymium/core/`      Contracts and shared utilities
- `dymium/runtime/`   SecureRuntime orchestration loop
- `dymium/adapters/`  Provider adapters (LLM, PII, MCP)
- `dymium/tools/`     Tool registry and execution boundaries
- `dymium/types/`     Generated types from `sdk/spec`
- `examples/`         Usage stubs and reference flows

## Quick start (factory-based)

```python
from dymium import SecureRuntime, RuntimeConfig

config = RuntimeConfig(
    llm="openai",
    pii="presidio",
    llm_config={"api_key": "..."},
    pii_config={"base_url": "http://localhost:5000"},
    mcp={"base_url": "http://localhost:7000"},
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
