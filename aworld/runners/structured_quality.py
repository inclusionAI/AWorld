"""Bounded, capability-neutral structured-output quality observations.

The long-horizon controller must not know how a particular parser repairs a
table.  It does, however, need a small common vocabulary for the observable
state that parser and validation tools return.  This module projects JSON Tool
output into an advisory quality debt and can independently inspect the public
candidate for a structured representation of the declared tables.

All observations remain advisory: they can keep a repair window open or force
an honest uncertain submission, but they never constitute verifier success.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from aworld.core.context.compiler import semantic_fingerprint

STRUCTURED_QUALITY_SCHEMA = "aworld.structured-quality-observation/v1"
_MAX_RESULT_CHARS = 512 * 1024
_MAX_DOCUMENTS = 16
_MAX_NODES = 4096
_MAX_PUBLIC_BYTES = 4 * 1024 * 1024
_TABLE_REASON = re.compile(
    r"(?:table|cell|grid).*(?:unusable|prose|invalid|missing|empty)|"
    r"(?:unusable|prose|invalid|missing|empty).*(?:table|cell|grid)|"
    r"structured_block_meta_prose",
    re.IGNORECASE,
)
_MARKDOWN_TABLE_SEPARATOR = re.compile(
    r"(?m)^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_HTML_TABLE = re.compile(r"<(?:table|tr|td|th)\b", re.IGNORECASE)


def _json_documents(value: Any) -> tuple[Any, ...]:
    """Extract bounded JSON documents from one Tool result projection."""

    documents: list[Any] = []
    seen_text: set[str] = set()

    def visit(item: Any, depth: int = 0) -> None:
        if depth > 5 or len(documents) >= _MAX_DOCUMENTS:
            return
        if isinstance(item, Mapping):
            documents.append(dict(item))
            for key in ("content", "message", "stdout", "output", "result"):
                if key in item:
                    visit(item[key], depth + 1)
            return
        if isinstance(item, (list, tuple)):
            for child in item[:16]:
                visit(child, depth + 1)
            return
        if not isinstance(item, str) or not item or len(item) > _MAX_RESULT_CHARS:
            return
        if item in seen_text:
            return
        seen_text.add(item)
        candidates = [item]
        candidates.extend(line for line in item.splitlines() if line.lstrip().startswith(("{", "[")))
        for candidate in candidates[:32]:
            try:
                parsed = json.loads(candidate)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            visit(parsed, depth + 1)

    visit(value)
    return tuple(documents[:_MAX_DOCUMENTS])


def _bounded_nodes(values: Iterable[Any]) -> Iterable[Any]:
    stack = list(values)[-64:]
    visited = 0
    while stack and visited < _MAX_NODES:
        value = stack.pop()
        visited += 1
        yield value
        if isinstance(value, Mapping):
            stack.extend(list(value.values())[-64:])
        elif isinstance(value, (list, tuple)):
            stack.extend(list(value)[-64:])


def _non_negative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _table_usable(value: Mapping[str, Any]) -> bool:
    if value.get("usable") is False:
        return False
    status = str(value.get("status") or "").strip().lower()
    if status in {"unusable", "invalid", "quality_open", "native_preserved"}:
        return False
    table = value.get("table")
    if isinstance(table, Mapping):
        for key in ("cells", "rows", "grid", "body"):
            content = table.get(key)
            if isinstance(content, (list, tuple)) and content:
                return True
    for key in ("cells", "rows", "grid"):
        content = value.get(key)
        if isinstance(content, (list, tuple)) and content:
            return True
    text = value.get("text") or value.get("markdown") or value.get("html")
    if isinstance(text, str):
        return bool(_MARKDOWN_TABLE_SEPARATOR.search(text) or _HTML_TABLE.search(text))
    return value.get("usable") is True


def observe_structured_quality(
    action_results: Iterable[Any],
    *,
    trusted_call_ids: set[str],
) -> dict[str, Any] | None:
    """Project successful, Sandbox-bound result JSON into quality debt."""

    documents: list[Any] = []
    for result in action_results:
        if not isinstance(result, Mapping):
            continue
        call_id = result.get("tool_call_id")
        if (
            not isinstance(call_id, str)
            or call_id not in trusted_call_ids
            or result.get("success") is not True
            or result.get("error")
        ):
            continue
        documents.extend(_json_documents(result))
        if len(documents) >= _MAX_DOCUMENTS:
            break
    if not documents:
        return None

    required = 0
    usable = 0
    reasons: set[str] = set()
    evidence_seen = False
    identities: set[str] = set()
    for node in _bounded_nodes(documents):
        if not isinstance(node, Mapping):
            continue
        for key in ("result_id", "task_id", "trace_id"):
            value = node.get(key)
            if isinstance(value, str) and value:
                identities.add(value[:256])
        schema = node.get("schema_version")
        if schema == STRUCTURED_QUALITY_SCHEMA:
            declared = _non_negative_int(node.get("required_table_count"))
            valid = _non_negative_int(node.get("usable_table_count"))
            if declared is not None and valid is not None and valid <= declared:
                evidence_seen = True
                required = max(required, declared)
                usable = max(usable, valid)
            for reason in node.get("reason_codes") or ():
                if isinstance(reason, str) and reason:
                    reasons.add(reason[:128])
        declared = _non_negative_int(node.get("declared"))
        valid = _non_negative_int(node.get("usable"))
        if declared is not None and valid is not None and valid <= declared:
            # ``declared``/``usable`` is the common bounded quality-count
            # vocabulary used by capability adapters.  Objects with unrelated
            # booleans do not pass the integer/count checks above.
            keys = {str(key).lower() for key in node}
            if (
                "tables" in keys
                or any("table" in key for key in keys)
                or {"declared", "usable"}.issubset(keys)
            ):
                evidence_seen = True
                required = max(required, declared)
                usable = max(usable, valid)
        kind = str(node.get("type") or node.get("kind") or "").strip().lower()
        if kind in {"table", "structured_table", "cell_grid"}:
            evidence_seen = True
            required += 1
            if _table_usable(node):
                usable += 1
            else:
                reasons.add("structured_table_unusable")
        for key in ("reason", "code", "quality_reason_code"):
            reason = node.get(key)
            if isinstance(reason, str) and _TABLE_REASON.search(reason):
                evidence_seen = True
                reasons.add(reason[:128])

    if not evidence_seen:
        return None
    if required > usable and not reasons:
        reasons.add("structured_table_coverage_incomplete")
    status = "open" if reasons or usable < required else "clear"
    projection = {
        "schema_version": STRUCTURED_QUALITY_SCHEMA,
        "status": status,
        "required_table_count": min(required, 10_000),
        "usable_table_count": min(usable, 10_000),
        "reason_codes": sorted(reasons)[:16],
        "source_identity": semantic_fingerprint(sorted(identities)),
    }
    projection["evidence_fingerprint"] = semantic_fingerprint(projection)
    return projection


def _public_paths(context: Any) -> tuple[Path, ...]:
    contract = getattr(context, "context_info", {}).get("public_deliverable_contract")
    artifacts = contract.get("artifacts") if isinstance(contract, Mapping) else None
    paths: list[Path] = []
    if not isinstance(artifacts, list):
        return ()
    for item in artifacts[:16]:
        raw = item.get("path") if isinstance(item, Mapping) else None
        if not isinstance(raw, str) or not raw:
            continue
        path = Path(raw)
        try:
            metadata = path.stat()
        except OSError:
            continue
        if path.is_file() and metadata.st_size <= _MAX_PUBLIC_BYTES:
            paths.append(path)
    return tuple(paths)


def inspect_public_structured_quality(
    context: Any,
    *,
    required_table_count: int,
) -> dict[str, Any] | None:
    """Independently check public outputs for a real table representation."""

    if required_table_count <= 0:
        return None
    representation_counts: dict[str, int] = {}
    inspected: list[dict[str, Any]] = []
    remaining = _MAX_PUBLIC_BYTES
    for path in _public_paths(context):
        try:
            data = path.read_bytes()[: remaining + 1]
        except OSError:
            continue
        if len(data) > remaining:
            break
        remaining -= len(data)
        text = data.decode("utf-8", errors="replace")
        count = len(_MARKDOWN_TABLE_SEPARATOR.findall(text))
        count += len(re.findall(r"<table\b", text, re.IGNORECASE))
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = None
            if payload is not None:
                count += sum(
                    1
                    for node in _bounded_nodes((payload,))
                    if isinstance(node, Mapping)
                    and str(node.get("type") or node.get("kind") or "").lower()
                    in {"table", "structured_table", "cell_grid"}
                    and _table_usable(node)
                )
        representation = (
            "structured"
            if path.suffix.lower() == ".json"
            else "rendered"
            if path.suffix.lower() in {".md", ".markdown", ".html", ".htm"}
            else "other"
        )
        representation_counts[representation] = max(
            representation_counts.get(representation, 0),
            count,
        )
        inspected.append(
            {
                "path_id": semantic_fingerprint(os.fspath(path)),
                "content_id": "sha256:" + hashlib.sha256(data).hexdigest(),
                "usable_table_count": count,
            }
        )
    if not inspected:
        return None
    if {"structured", "rendered"}.issubset(representation_counts):
        usable = min(
            representation_counts["structured"],
            representation_counts["rendered"],
        )
    else:
        usable = max(representation_counts.values(), default=0)
    projection = {
        "schema_version": STRUCTURED_QUALITY_SCHEMA,
        "status": "clear" if usable >= required_table_count else "open",
        "required_table_count": required_table_count,
        "usable_table_count": min(usable, 10_000),
        "reason_codes": (
            [] if usable >= required_table_count else ["public_structured_table_missing"]
        ),
        "public_evidence_fingerprint": semantic_fingerprint(inspected),
    }
    projection["evidence_fingerprint"] = semantic_fingerprint(projection)
    return projection


__all__ = [
    "STRUCTURED_QUALITY_SCHEMA",
    "inspect_public_structured_quality",
    "observe_structured_quality",
]
