"""Project executed task text without importing answers or tool observations."""

from __future__ import annotations

import ast
import json
from typing import Any, Mapping


def task_context_text(value: Any) -> str | None:
    """Keep only textual task-input fields recognized by the replay executor.

    Never serialize an arbitrary mapping: source cases may also contain the
    original trajectory, reference answer, or tool-result transport state.
    Prompt-size limits remain the evaluator's responsibility, as for questions
    recovered from an uncompressed trajectory.
    """

    parsed = value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.startswith("{") and stripped.endswith("}"):
            for loader in (json.loads, ast.literal_eval):
                try:
                    decoded = loader(stripped)
                except (ValueError, SyntaxError, TypeError):
                    continue
                if isinstance(decoded, Mapping):
                    parsed = decoded
                    break
        if isinstance(parsed, str):
            return parsed
    if not isinstance(parsed, Mapping) or parsed.get("action_result"):
        return None
    for key in ("content", "task", "prompt", "input"):
        text = parsed.get(key)
        if isinstance(text, str):
            # Match the executor's first-string precedence; a later fallback
            # field was not executed when an earlier one contained empty text.
            return text if text.strip() else None
    return None
