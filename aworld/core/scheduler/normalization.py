# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""Normalization helpers shared by cron scheduler components."""

from typing import Any, List


def normalize_tool_names(value: Any) -> List[str]:
    """Normalize persisted or user-supplied tool names into a clean list."""
    if value is None:
        return []

    if isinstance(value, list):
        normalized = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            if "," in text:
                normalized.extend(part.strip() for part in text.split(",") if part.strip())
            else:
                normalized.append(text)
        return normalized

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if "," in stripped:
            return [part.strip() for part in stripped.split(",") if part.strip()]
        return [stripped]

    text = str(value).strip()
    return [text] if text else []
