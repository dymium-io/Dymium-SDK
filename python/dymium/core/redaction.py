"""Redaction engine contract."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Protocol


class RedactionEngineProtocol(Protocol):
    def placeholderize(self, text: str, entities: Iterable[Dict[str, Any]], placeholder_map: Dict[str, str]) -> Dict[str, Any]:
        """Return sanitized text and updated placeholder map."""
        raise NotImplementedError

    def resolve_placeholders(self, text: str, placeholder_map: Dict[str, str]) -> str:
        """Resolve placeholders to originals (tool boundary only)."""
        raise NotImplementedError

    def re_placeholderize(self, tool_output: Any, placeholder_map: Dict[str, str]) -> Any:
        """Re-apply placeholders to tool outputs."""
        raise NotImplementedError
