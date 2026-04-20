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


def resolve_effective_tool_names(agent_name: str, tool_names: List[str]) -> List[str]:
    """
    Return the effective tool allowlist for a cron job.

    Aworld/root-agent cron tasks should not be constrained by persisted tool
    allowlists because cron-created automation is often open-ended and requires
    the runtime agent to choose the necessary tools dynamically.
    """
    if agent_name == "Aworld":
        return []
    return tool_names
