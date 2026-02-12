"""Redaction engine (placeholder generation + replacement).

This is a minimal reference implementation for placeholder behavior.
"""
from __future__ import annotations

import re
import secrets
from typing import Any, Dict, Iterable, List

PLACEHOLDER_REGEX = re.compile(r"\bPH_[A-Z]+_[A-Z0-9]{5}\b")
CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _gen_id(length: int = 5) -> str:
    return "".join(secrets.choice(CROCKFORD32) for _ in range(length))


def _is_placeholder(value: str) -> bool:
    return bool(PLACEHOLDER_REGEX.search(value))


def _reverse_map(placeholder_map: Dict[str, str]) -> Dict[str, str]:
    return {v: k for k, v in placeholder_map.items()}


def _sorted_entities(entities: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered = [e for e in entities if isinstance(e, dict)]
    # sort descending to avoid offset shifts
    return sorted(filtered, key=lambda e: e.get("beginOffset", -1), reverse=True)


class RedactionEngine:
    def __init__(self, id_length: int = 5) -> None:
        self.id_length = id_length

    def placeholderize(
        self,
        text: str,
        entities: Iterable[Dict[str, Any]],
        placeholder_map: Dict[str, str],
    ) -> Dict[str, Any]:
        if not text:
            return {"text": "", "placeholder_map": placeholder_map}

        rev = _reverse_map(placeholder_map)
        new_text = text

        for ent in _sorted_entities(entities):
            start = ent.get("beginOffset")
            end = ent.get("endOffset")
            etype = (ent.get("type") or "VALUE").upper()
            if not isinstance(start, int) or not isinstance(end, int):
                continue
            if start < 0 or end > len(new_text) or end <= start:
                continue

            snippet = new_text[start:end]
            placeholder = rev.get(snippet)
            if not placeholder:
                placeholder = f"PH_{etype}_{_gen_id(self.id_length)}"
                placeholder_map[placeholder] = snippet
                rev[snippet] = placeholder

            new_text = new_text[:start] + placeholder + new_text[end:]

        return {"text": new_text, "placeholder_map": placeholder_map}

    def resolve_placeholders(self, text: str, placeholder_map: Dict[str, str]) -> str:
        if not text or not placeholder_map:
            return text

        # Replace longer placeholders first
        for placeholder in sorted(placeholder_map.keys(), key=len, reverse=True):
            original = placeholder_map[placeholder]
            text = text.replace(placeholder, original)
        return text

    def re_placeholderize(self, tool_output: Any, placeholder_map: Dict[str, str]) -> Any:
        """Conservatively placeholderize all scalar outputs."""
        if isinstance(tool_output, dict):
            return {k: self.re_placeholderize(v, placeholder_map) for k, v in tool_output.items()}
        if isinstance(tool_output, list):
            return [self.re_placeholderize(v, placeholder_map) for v in tool_output]
        if isinstance(tool_output, str):
            if _is_placeholder(tool_output):
                return tool_output
            # map string to placeholder
            rev = _reverse_map(placeholder_map)
            placeholder = rev.get(tool_output)
            if not placeholder:
                placeholder = f"PH_VALUE_{_gen_id(self.id_length)}"
                placeholder_map[placeholder] = tool_output
            return placeholder
        if isinstance(tool_output, (int, float, bool)):
            # represent scalars as placeholder strings
            return self.re_placeholderize(str(tool_output), placeholder_map)
        return tool_output
