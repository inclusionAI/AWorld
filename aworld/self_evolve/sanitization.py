from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping


_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer|basic)\s+[A-Za-z0-9._~+/\-]+=*"),
    re.compile(
        r"(?i)(secret|token|api[_-]?key|password|authorization|cookie)"
        r"\s*[:=]\s*(?:bearer|basic)?\s*\S+"
    ),
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
)
_LOCAL_PATH_PATTERNS = (
    re.compile(r"(?<![\w.-])/(?:Users|private|var|tmp|home)/[^\s,;:'\")\]}]+"),
    re.compile(r"~/?[^\s,;:'\")\]}]+"),
)
_UNTRUSTED_INSTRUCTION_PATTERNS = (
    re.compile(r"(?i)\bignore (all )?(previous|prior|above) (instructions|messages)\b"),
    re.compile(r"(?i)\bdisregard (all )?(previous|prior|above) (instructions|messages)\b"),
    re.compile(r"(?i)\bsystem prompt\b"),
    re.compile(r"(?i)\bdeveloper message\b"),
)


def sanitize_text(value: Any, *, max_chars: int | None = None) -> str:
    text = str(value or "")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("<REDACTED_SECRET>", text)
    for pattern in _LOCAL_PATH_PATTERNS:
        text = pattern.sub("<LOCAL_PATH>", text)
    for pattern in _UNTRUSTED_INSTRUCTION_PATTERNS:
        text = pattern.sub("<UNTRUSTED_INSTRUCTION>", text)
    text = _normalize_control_chars(text).strip()
    if max_chars is not None and len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def sanitize_metric_value(value: Any, *, max_chars: int = 240) -> Any:
    if isinstance(value, str):
        return sanitize_text(value, max_chars=max_chars)
    if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
        return value
    if isinstance(value, list):
        return [sanitize_metric_value(item, max_chars=max_chars) for item in value[:8]]
    if isinstance(value, tuple):
        return tuple(sanitize_metric_value(item, max_chars=max_chars) for item in value[:8])
    if isinstance(value, Mapping):
        return {
            str(key): sanitize_metric_value(item, max_chars=max_chars)
            for key, item in list(value.items())[:16]
        }
    return sanitize_text(value, max_chars=max_chars)


def sanitize_path_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    name = path.name or "<LOCAL_PATH>"
    if _looks_private_path(text):
        parent = path.parent.name
        return f"<LOCAL_PATH>/{parent}/{name}" if parent else f"<LOCAL_PATH>/{name}"
    return sanitize_text(text, max_chars=240)


def _looks_private_path(value: str) -> bool:
    return (
        value.startswith("/")
        or value.startswith("~")
        or "/Users/" in value
        or "/private/" in value
    )


def _normalize_control_chars(value: str) -> str:
    return "".join(
        character if character == "\n" or character == "\t" or ord(character) >= 32 else " "
        for character in value
    )
