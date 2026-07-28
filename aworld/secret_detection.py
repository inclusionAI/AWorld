from __future__ import annotations

import base64
import binascii
import re
from typing import Any, Callable


AUTH_SCHEME_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(?P<scheme>bearer|basic)\s+"
    r"(?P<credential>[A-Za-z0-9._~+/\-]+=*)"
)
AUTHORIZATION_SCHEME_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\bauthorization\s*[:=]\s*"
    r"(?P<scheme>bearer|basic)\s+"
    r"(?P<credential>[^\s,;]+)"
    r"(?=(?P<tail>[^\r\n]*))"
)
AUTHORIZATION_OPAQUE_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\bauthorization\s*[:=]\s*"
    r"(?!(?:bearer|basic)\b)"
    r"(?P<credential>[^\s,;]+)"
    r"(?=(?P<tail>[^\r\n]*))"
)
NAMED_SECRET_PATTERN = re.compile(
    r"(?i)(secret|token|api[_-]?key|password|cookie)"
    r"\s*[:=]\s*(?:bearer|basic)?\s*\S+"
)
SK_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{12,}")

_JWT_PATTERN = re.compile(
    r"[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}"
)
_OPAQUE_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~+/\-]+=*")
_PLACEHOLDER_PATTERN = re.compile(
    r"(?:"
    r"<[^>]+>|\[[^\]]+\]|\{[^}]+\}|\$\{[^}]+\}|"
    r"\$[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z_][A-Za-z0-9_]*%|"
    r"\.{3,}"
    r")"
)


def _unquoted(value: str) -> str:
    stripped = value.strip()
    if (
        len(stripped) >= 2
        and stripped[0] == stripped[-1]
        and stripped[0] in {'"', "'"}
    ):
        return stripped[1:-1]
    return stripped


def _is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_PATTERN.fullmatch(_unquoted(value)))


def _is_basic_credential(value: str) -> bool:
    token = _unquoted(value)
    if _is_placeholder(token) or not re.fullmatch(
        r"[A-Za-z0-9+/]+={0,2}",
        token,
    ):
        return False
    try:
        decoded = base64.b64decode(
            token + "=" * (-len(token) % 4),
            validate=True,
        )
    except (ValueError, binascii.Error):
        return False
    return b":" in decoded


def _is_bearer_credential(value: str) -> bool:
    token = _unquoted(value)
    if _is_placeholder(token) or not _OPAQUE_TOKEN_PATTERN.fullmatch(token):
        return False
    if token.endswith("..."):
        return False
    if _JWT_PATTERN.fullmatch(token):
        return True
    has_letter = any(character.isalpha() for character in token)
    has_digit = any(character.isdigit() for character in token)
    has_strong_token_punctuation = any(
        character in "_~+/-=" for character in token
    )
    has_mixed_case = any(character.islower() for character in token) and any(
        character.isupper() for character in token
    )
    return has_letter and len(token) >= 8 and (
        has_digit
        or has_strong_token_punctuation
        or (
            len(token) >= 12
            and "." in token
        )
        or (len(token) >= 20 and has_mixed_case)
    )


def _bare_auth_match_is_sensitive(match: re.Match[str]) -> bool:
    scheme = match.group("scheme").casefold()
    credential = match.group("credential")
    return (
        _is_basic_credential(credential)
        if scheme == "basic"
        else _is_bearer_credential(credential)
    )


def _explicit_auth_match_is_sensitive(match: re.Match[str]) -> bool:
    credential = match.group("credential")
    scheme = match.group("scheme").casefold()
    if scheme == "basic":
        return _is_basic_credential(credential)
    if _is_bearer_credential(credential):
        return True
    tail = match.group("tail").strip()
    return (
        (not tail or tail.startswith(("#", "//")))
        and not _is_placeholder(credential)
    )


def _opaque_auth_match_is_sensitive(match: re.Match[str]) -> bool:
    if _is_bearer_credential(match.group("credential")):
        return True
    tail = match.group("tail").strip()
    if tail and not tail.startswith(("#", "//")):
        return False
    return False


def authorization_value_contains_sensitive_literal(value: Any) -> bool:
    """Classify a complete Authorization value, including quoted source values."""

    text = _unquoted(str(value or ""))
    scheme_match = re.fullmatch(
        r"(?i)(?P<scheme>bearer|basic)\s+(?P<credential>\S+)",
        text,
    )
    if scheme_match is not None:
        credential = scheme_match.group("credential")
        return (
            _is_basic_credential(credential)
            if scheme_match.group("scheme").casefold() == "basic"
            else not _is_placeholder(credential)
        )
    return _is_bearer_credential(text)


def _has_sensitive_match(
    pattern: re.Pattern[str],
    text: str,
    predicate: Callable[[re.Match[str]], bool],
) -> bool:
    return any(predicate(match) for match in pattern.finditer(text))


def redact_sensitive_literals(
    value: Any,
    *,
    replacement: str = "<REDACTED_SECRET>",
    include_named: bool = True,
) -> str:
    """Redact concrete credential syntax while retaining documentation prose."""

    text = str(value or "")
    for pattern, predicate in (
        (
            AUTHORIZATION_SCHEME_CREDENTIAL_PATTERN,
            _explicit_auth_match_is_sensitive,
        ),
        (
            AUTHORIZATION_OPAQUE_CREDENTIAL_PATTERN,
            _opaque_auth_match_is_sensitive,
        ),
        (AUTH_SCHEME_CREDENTIAL_PATTERN, _bare_auth_match_is_sensitive),
    ):
        text = pattern.sub(
            lambda match: (
                replacement if predicate(match) else match.group(0)
            ),
            text,
        )
    if include_named:
        text = NAMED_SECRET_PATTERN.sub(replacement, text)
    return SK_SECRET_PATTERN.sub(replacement, text)


def contains_sensitive_literal(value: Any) -> bool:
    """Return whether text contains a syntactically concrete credential."""

    text = str(value or "")
    return (
        _has_sensitive_match(
            AUTHORIZATION_SCHEME_CREDENTIAL_PATTERN,
            text,
            _explicit_auth_match_is_sensitive,
        )
        or _has_sensitive_match(
            AUTHORIZATION_OPAQUE_CREDENTIAL_PATTERN,
            text,
            _opaque_auth_match_is_sensitive,
        )
        or _has_sensitive_match(
            AUTH_SCHEME_CREDENTIAL_PATTERN,
            text,
            _bare_auth_match_is_sensitive,
        )
        or NAMED_SECRET_PATTERN.search(text) is not None
        or SK_SECRET_PATTERN.search(text) is not None
    )
