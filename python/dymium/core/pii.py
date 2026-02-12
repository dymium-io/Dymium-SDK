"""PII engine contract."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Protocol

from dymium.types import DetectedEntity


class PIIEngine(Protocol):
    def detect(
        self,
        text: str,
        options: Optional[Dict[str, Any]] = None,
    ) -> Iterable[DetectedEntity | Dict[str, Any]]:
        """Return detected PII entities in canonical DetectedEntity shape."""
        raise NotImplementedError

    def normalize(
        self,
        entities: Iterable[DetectedEntity | Dict[str, Any]],
    ) -> Iterable[DetectedEntity | Dict[str, Any]]:
        """Normalize provider-specific entities into canonical DetectedEntity shape."""
        raise NotImplementedError
