"""Stateful session wrapper for multi-turn conversations."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from dymium.runtime.secure_runtime import SecureRuntime


@dataclass
class SecureSession:
    runtime: SecureRuntime
    history: List[Dict[str, Any]] = field(default_factory=list)
    placeholder_map: Dict[str, str] = field(default_factory=dict)

    def run(self, content: str, **kwargs: Any) -> Dict[str, Any]:
        request = {
            "messages": [
                *list(self.history),
                {"role": "user", "content": content},
            ],
            "placeholderMap": dict(self.placeholder_map),
        }
        request.update(kwargs)

        result = self.runtime.run(request)

        # Update history + placeholder map
        if result.get("messages"):
            # messages already include prior history + new user + tool + assistant
            self.history = result["messages"]
        else:
            # Fallback: append assistant text only
            self.history.append({"role": "assistant", "content": result.get("text", "")})

        if result.get("placeholder_map"):
            self.placeholder_map = result["placeholder_map"]

        return result
