"""Regex-based PII detection helpers."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


def detect_regex_entities(text: str, rules: Any | None = None) -> List[Dict[str, Any]]:
    if not text:
        return []
    if isinstance(rules, dict):
        if "pattern" in rules:
            rules = [rules]
        else:
            rules = rules.get("regex_rules") or rules.get("regexRules") or []
    if isinstance(rules, dict):
        rules = [rules]
    if not isinstance(rules, list):
        return []

    entities: List[Dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        pattern = rule.get("pattern")
        if not pattern:
            continue
        flags = _parse_flags(rule.get("flags"))
        group = rule.get("group", 0)
        etype = rule.get("type") or "REGEX"
        score = rule.get("score", 1.0)
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            raise ValueError(f"Invalid regex pattern: {pattern}") from exc

        for match in regex.finditer(text):
            try:
                start, end = match.span(group)
            except IndexError:
                start, end = match.span(0)
            snippet = match.group(group) if group is not None else match.group(0)
            entities.append({
                "type": etype,
                "score": score,
                "beginOffset": start,
                "endOffset": end,
                "text": snippet,
            })

    return entities


def _parse_flags(flags: Any) -> int:
    if flags is None:
        return 0
    if isinstance(flags, int):
        return flags
    if not isinstance(flags, str):
        return 0
    out = 0
    if "i" in flags:
        out |= re.IGNORECASE
    if "m" in flags:
        out |= re.MULTILINE
    if "s" in flags:
        out |= re.DOTALL
    return out
