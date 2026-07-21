from __future__ import annotations

import hashlib
import json
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
_SOURCE_QUOTED_SECRET_PATTERN = re.compile(
    r"(?i)(\b(?:secret|token|api[_-]?key|password|authorization|cookie)"
    r"\s*[:=]\s*)([\"'])([^\n\"']*)([\"'])"
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

_PUBLIC_SENSITIVE_FIELDS = frozenset(
    {
        "authorization",
        "body",
        "content",
        "cookie",
        "declared_response_contains",
        "expected_preview",
        "expected_response",
        "password",
        "private_context",
        "previous_expected_preview",
        "raw_response",
        "recorded_response",
        "response",
        "response_contains",
        "response_preview",
        "secret",
        "token",
    }
)
_PUBLIC_PROJECTION_MAX_DEPTH = 8
_PUBLIC_PROJECTION_MAX_MAPPING_ITEMS = 64
_PUBLIC_PROJECTION_MAX_SEQUENCE_ITEMS = 100
_PUBLIC_FINGERPRINT_MAX_NODES = 4_000_000
_PUBLIC_IDENTITY_DIGEST_FIELDS = {
    "capability_id": "capability_identity_digest",
    "requirement_id": "requirement_identity_digest",
    "contract_fingerprint": "contract_identity_digest",
}


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


def sanitize_source_text(value: Any, *, max_chars: int | None = None) -> str:
    """Sanitize bounded source code without destroying executable expressions.

    Candidate repair packages are already generated artifacts, but can still
    contain copied literal credentials. Redact those literals and universal
    credential forms while preserving dynamic assignments such as
    ``token = match.group()`` that are required to repair the source.
    """

    text = str(value or "")
    text = _SECRET_PATTERNS[0].sub("<REDACTED_SECRET>", text)
    text = _SECRET_PATTERNS[2].sub("<REDACTED_SECRET>", text)
    text = _SOURCE_QUOTED_SECRET_PATTERN.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}"
            f"<REDACTED_SECRET>{match.group(4)}"
        ),
        text,
    )
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


def public_diagnostic_projection(
    value: Any,
    *,
    max_chars: int = 1_000,
    max_depth: int = _PUBLIC_PROJECTION_MAX_DEPTH,
) -> Any:
    """Create the only persistence-safe view of diagnostic data.

    This projection is intentionally type-aware.  An executable repair
    conformance contract is converted through ``to_public_dict`` even when it
    is accidentally placed several containers below a gate or optimizer
    diagnostic.  Payload-bearing fields are represented only by a stable
    fingerprint and a non-content shape.  When a display budget is reached we
    summarize the *complete* remaining value rather than stringifying a
    container (which used to reproduce secrets in reports).
    """

    return _project_public_diagnostic(
        value,
        depth=0,
        max_chars=max_chars,
        max_depth=max_depth,
    )


def _project_public_diagnostic(
    value: Any,
    *,
    depth: int,
    max_chars: int,
    max_depth: int,
) -> Any:
    typed_failure = _typed_replay_failure_public_projection(value)
    if typed_failure is not None:
        # Typed failure transports are already raw-free, integrity checked,
        # and cardinality exact.  Re-projecting their complete identity sets
        # through generic sequence budgets would invalidate aggregate digests.
        return typed_failure
    public_contract = _repair_conformance_public_projection(value)
    if public_contract is not None:
        value = public_contract
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _sanitize_public_diagnostic_text(value, max_chars=max_chars)
    if isinstance(value, bytes):
        return _private_value_summary(value)
    if depth >= max_depth:
        return _budget_summary(value, reason="depth")
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        items = sorted(value.items(), key=lambda pair: _public_key(pair[0]))
        for raw_key, item in items[:_PUBLIC_PROJECTION_MAX_MAPPING_ITEMS]:
            key = sanitize_text(_public_key(raw_key), max_chars=120)
            identity_digest_field = _PUBLIC_IDENTITY_DIGEST_FIELDS.get(key)
            if identity_digest_field is not None:
                if item is not None:
                    identity_digest = hashlib.sha256(
                        str(item).strip().encode("utf-8")
                    ).hexdigest()
                    serialized_digest = value.get(identity_digest_field)
                    if (
                        serialized_digest is not None
                        and serialized_digest != identity_digest
                    ):
                        raise ValueError(
                            f"{identity_digest_field} conflicts with raw identity"
                        )
                    projected[identity_digest_field] = identity_digest
                continue
            if _sensitive_public_field(key):
                summary = _private_value_summary(item)
                projected[key + "_fingerprint"] = summary["fingerprint"]
                projected[key + "_shape"] = summary["shape"]
                continue
            projected[key] = _project_public_diagnostic(
                item,
                depth=depth + 1,
                max_chars=max_chars,
                max_depth=max_depth,
            )
        if len(items) > _PUBLIC_PROJECTION_MAX_MAPPING_ITEMS:
            projected["_truncated_summary"] = _budget_summary(
                value,
                reason="mapping_items",
            )
        return projected
    if isinstance(value, (list, tuple)):
        projected_items = [
            _project_public_diagnostic(
                item,
                depth=depth + 1,
                max_chars=max_chars,
                max_depth=max_depth,
            )
            for item in value[:_PUBLIC_PROJECTION_MAX_SEQUENCE_ITEMS]
        ]
        if len(value) > _PUBLIC_PROJECTION_MAX_SEQUENCE_ITEMS:
            projected_items.append(_budget_summary(value, reason="sequence_items"))
        return projected_items
    return {
        "kind": "unsupported_public_value",
        "type": f"{type(value).__module__}.{type(value).__qualname__}",
        "fingerprint": _stable_public_fingerprint(value),
    }


def _typed_replay_failure_public_projection(
    value: Any,
) -> Mapping[str, Any] | None:
    """Return the verified public transport for typed replay failures.

    Imports stay local because failure events use ``sanitize_text`` while
    constructing their bounded private representation.  Typed schemas fail
    closed here: a forged digest must never be persisted as if it were a
    trustworthy public diagnostic.
    """

    value_type = type(value)
    is_typed_object = (
        value_type.__module__ == "aworld.self_evolve.failure_events"
        and value_type.__qualname__
        in {"AggregatedReplayFailure", "ReplayFailureEvent"}
    )
    schema_version = (
        str(value.get("schema_version") or "")
        if isinstance(value, Mapping)
        else ""
    )
    if not is_typed_object and not schema_version.startswith(
        "aworld.self_evolve.replay_failure"
    ):
        return None

    from aworld.self_evolve.failure_events import (
        AggregatedReplayFailure,
        ReplayFailureEvent,
        ReplayFailureObservation,
        aggregate_replay_failure_observations,
    )

    if isinstance(value, AggregatedReplayFailure):
        aggregate = value
    elif isinstance(value, ReplayFailureEvent):
        aggregate = aggregate_replay_failure_observations(
            (ReplayFailureObservation(event=value),)
        )[0]
    elif isinstance(value, Mapping) and "replay_failure_aggregate" in schema_version:
        aggregate = AggregatedReplayFailure.from_dict(value)
    elif isinstance(value, Mapping):
        event = ReplayFailureEvent.from_dict(value)
        aggregate = aggregate_replay_failure_observations(
            (ReplayFailureObservation(event=event),)
        )[0]
    else:  # pragma: no cover - guarded by the exact type/schema checks above
        return None
    return aggregate.to_feedback_dict()


def _repair_conformance_public_projection(value: Any) -> Mapping[str, Any] | None:
    value_type = type(value)
    if (
        value_type.__module__ == "aworld.self_evolve.repair_conformance"
        and value_type.__qualname__ == "RepairConformanceContract"
    ):
        projector = getattr(value, "to_public_dict", None)
        if not callable(projector):
            raise TypeError("repair conformance contract lacks public projection")
        projected = projector()
        if not isinstance(projected, Mapping):
            raise TypeError("repair conformance public projection must be a mapping")
        return projected
    if not isinstance(value, Mapping):
        return None
    exact_probe = value.get("exact_probe")
    if (
        value.get("projection_schema_version") is None
        and isinstance(value.get("focus_candidate_id"), str)
        and isinstance(exact_probe, Mapping)
        and "expected_response" in exact_probe
    ):
        # Defensive recovery for an execution contract that was misplaced in
        # an untyped diagnostic mapping.  Do not instantiate the private type:
        # doing so here would create an import cycle with repair_conformance.
        projected = dict(value)
        expected = exact_probe.get("expected_response")
        expected_bytes = (
            expected.encode("utf-8") if isinstance(expected, str) else _canonical_bytes(expected)
        )
        public_probe = {
            key: nested
            for key, nested in exact_probe.items()
            if key != "expected_response"
        }
        public_probe.update(
            {
                "expected_response_fingerprint": (
                    "sha256:" + hashlib.sha256(expected_bytes).hexdigest()
                ),
                "expected_response_shape": _public_value_shape(expected),
            }
        )
        projected["projection_schema_version"] = (
            "aworld.self_evolve.repair_conformance.public.v1"
        )
        projected["exact_probe"] = public_probe
        return projected
    return None


def _sanitize_public_diagnostic_text(value: str, *, max_chars: int) -> str:
    """Remove payload previews embedded by legacy replay error messages."""

    text = re.sub(
        r"\bexpected_preview=(.*?)(?=\s+(?:response_bytes|response_sha256|"
        r"classification|required_change)=|$)",
        lambda match: (
            "expected_preview_fingerprint="
            + _stable_public_fingerprint(match.group(1).strip())
        ),
        value,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"\bresponse_preview=(.*?)(?=\s+(?:classification|required_change)=|$)",
        lambda match: (
            "response_preview_fingerprint="
            + _stable_public_fingerprint(match.group(1).strip())
        ),
        text,
        flags=re.DOTALL,
    )
    return sanitize_text(text, max_chars=max_chars)


def _sensitive_public_field(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    if normalized.endswith(("_fingerprint", "_sha256", "_bytes", "_shape")):
        return False
    return normalized in _PUBLIC_SENSITIVE_FIELDS or normalized.startswith(
        ("secret_", "token_", "password_", "private_")
    )


def _private_value_summary(value: Any) -> dict[str, Any]:
    return {
        "fingerprint": _stable_public_fingerprint(value),
        "shape": _public_value_shape(value),
    }


def _budget_summary(value: Any, *, reason: str) -> dict[str, Any]:
    return {
        "kind": "bounded_public_summary",
        "reason": reason,
        "fingerprint": _stable_public_fingerprint(value),
        "shape": _public_value_shape(value),
    }


def _public_value_shape(value: Any) -> dict[str, Any]:
    if value is None:
        return {"kind": "null"}
    if isinstance(value, bool):
        return {"kind": "boolean"}
    if isinstance(value, (int, float)):
        return {"kind": "number"}
    if isinstance(value, str):
        return {
            "kind": "text",
            "bytes": len(value.encode("utf-8")),
        }
    if isinstance(value, bytes):
        return {"kind": "bytes", "bytes": len(value)}
    if isinstance(value, Mapping):
        return {"kind": "mapping", "items": len(value)}
    if isinstance(value, (list, tuple)):
        return {"kind": "sequence", "items": len(value)}
    return {
        "kind": "object",
        "type": f"{type(value).__module__}.{type(value).__qualname__}",
    }


def _stable_public_fingerprint(value: Any) -> str:
    digest = hashlib.sha256()
    stack: list[tuple[str, Any]] = [("value", value)]
    visited_containers: set[int] = set()
    nodes = 0
    while stack:
        token, item = stack.pop()
        nodes += 1
        if nodes > _PUBLIC_FINGERPRINT_MAX_NODES:
            raise ValueError("public diagnostic fingerprint resource budget exceeded")
        if token == "raw":
            digest.update(item)
            continue
        if item is None:
            digest.update(b"null;")
        elif isinstance(item, bool):
            digest.update(b"true;" if item else b"false;")
        elif isinstance(item, int):
            digest.update(b"int:")
            digest.update(str(item).encode("ascii"))
            digest.update(b";")
        elif isinstance(item, float):
            digest.update(b"float:")
            digest.update(repr(item).encode("ascii"))
            digest.update(b";")
        elif isinstance(item, str):
            encoded = item.encode("utf-8")
            digest.update(b"str:")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        elif isinstance(item, bytes):
            digest.update(b"bytes:")
            digest.update(len(item).to_bytes(8, "big"))
            digest.update(item)
        elif isinstance(item, Mapping):
            identity = id(item)
            if identity in visited_containers:
                digest.update(b"cycle;")
                continue
            visited_containers.add(identity)
            digest.update(b"map{")
            stack.append(("raw", b"}"))
            items = sorted(item.items(), key=lambda pair: _public_key(pair[0]))
            for raw_key, nested in reversed(items):
                stack.append(("value", nested))
                stack.append(("value", _public_key(raw_key)))
        elif isinstance(item, (list, tuple)):
            identity = id(item)
            if identity in visited_containers:
                digest.update(b"cycle;")
                continue
            visited_containers.add(identity)
            digest.update(b"seq[")
            stack.append(("raw", b"]"))
            for nested in reversed(item):
                stack.append(("value", nested))
        else:
            digest.update(b"object:")
            digest.update(
                f"{type(item).__module__}.{type(item).__qualname__}".encode("utf-8")
            )
            digest.update(b";")
    return "sha256:" + digest.hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        return _stable_public_fingerprint(value).encode("ascii")


def _public_key(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value, allow_nan=False)
    return f"<{type(value).__module__}.{type(value).__qualname__}>"


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
