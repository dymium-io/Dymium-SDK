# Spec

Authoritative JSON Schemas live here. These schemas are the source of truth for all SDK types.

Important:
- The SDK defines **canonical internal schemas** (e.g., ChatRequest).
- Provider-specific formats (OpenAI, Anthropic, Gemini, etc.) are handled in adapters.

Current schemas:
- `detected_entity.schema.json`
- `chat_event.schema.json`
- `chat_request.schema.json`
- `tool_settings.schema.json`
- `tool_definition.schema.json`
- `tool_call.schema.json`
- `tool_result.schema.json`
- `placeholder_map.schema.json`
- `agent_event.schema.json`

Code generation scripts consume this directory and write language-specific types.
