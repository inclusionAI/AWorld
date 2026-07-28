from __future__ import annotations

import re
from typing import Any


AUTH_DESCRIPTION_TERMS = (
    r"auth(?:entication|orization)?|credentials?|header|http|jwt|"
    r"password|scheme|token|username"
)
AUTH_SCHEME_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(?:bearer|basic)\s+"
    rf"(?!(?:{AUTH_DESCRIPTION_TERMS})\b)"
    r"[A-Za-z0-9._~+/\-]+=*"
)
AUTHORIZATION_SCHEME_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\bauthorization\s*[:=]\s*"
    r"(?:bearer|basic)\s+"
    rf"(?!(?:{AUTH_DESCRIPTION_TERMS})\b)"
    r"\S+"
)
AUTHORIZATION_OPAQUE_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\bauthorization\s*[:=]\s*"
    r"(?!(?:bearer|basic)\b)"
    rf"(?!(?:{AUTH_DESCRIPTION_TERMS})\b)"
    r"\S+"
)
NAMED_SECRET_PATTERN = re.compile(
    r"(?i)(secret|token|api[_-]?key|password|cookie)"
    r"\s*[:=]\s*(?:bearer|basic)?\s*\S+"
)
SK_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{12,}")
SECRET_PATTERNS = (
    AUTH_SCHEME_CREDENTIAL_PATTERN,
    AUTHORIZATION_SCHEME_CREDENTIAL_PATTERN,
    AUTHORIZATION_OPAQUE_CREDENTIAL_PATTERN,
    NAMED_SECRET_PATTERN,
    SK_SECRET_PATTERN,
)


def contains_sensitive_literal(value: Any) -> bool:
    """Return whether text contains a concrete credential literal."""

    text = str(value or "")
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)
