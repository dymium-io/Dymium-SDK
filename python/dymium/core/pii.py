"""PII engine contract."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Protocol


class PIIEngine(Protocol):
    def detect(self, text: str, options: Optional[Dict[str, Any]] = None) -> Iterable[Dict[str, Any]]:
        """Return detected PII entities."""
        raise NotImplementedError

    def normalize(self, entities: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
        """Optional normalization step for entity types."""
        raise NotImplementedError
