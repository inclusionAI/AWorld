"""Bounded, domain-agnostic checks for public deliverable candidates.

The runtime must not mistake an explicitly named placeholder for delivery,
but this layer is not a task verifier.  It therefore rejects only files that
can be proven empty or whose entire small UTF-8 text is a narrow placeholder.
Binary, large, unreadable, and otherwise ambiguous non-empty files remain
eligible so domain-specific correctness stays with the task verifier.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import stat as stat_module


_MAX_PLACEHOLDER_INSPECTION_BYTES = 64 * 1024
_PLACEHOLDER_LINE_COMMENT = re.compile(r"^(?:#|//|--|;)\s*(.*?)\s*$", re.DOTALL)
_PLACEHOLDER_BLOCK_COMMENT = re.compile(
    r"^(?:/\*\s*(.*?)\s*\*/|<!--\s*(.*?)\s*-->)$",
    re.DOTALL,
)
_PLACEHOLDER_FENCE = re.compile(
    r"^```(?:text|txt|plaintext)?\s*\n?(.*?)\n?```$",
    re.IGNORECASE | re.DOTALL,
)
_EXACT_PLACEHOLDER_TEXT = frozenset(
    {
        "tbd",
        "todo",
        "placeholder",
        "to be determined",
        "to be done",
        "not implemented",
        "implementation pending",
    }
)
_QUALIFIED_PLACEHOLDER_TEXT = re.compile(
    r"^(?:todo|tbd)(?:\s*(?::|-)\s*|\s+)"
    r"(?:implement|fill(?:\s+in)?|replace|complete|finish|later|pending|placeholder)"
    r"(?:\s+.*)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PublicDeliverableInspection:
    """A finite, path-free classification of one declared output file."""

    exists: bool
    candidate_eligible: bool
    rejection_reason: str | None = None


def _unwrap_placeholder_text(value: str) -> str:
    """Remove presentation-only wrappers around a whole-file placeholder."""

    candidate = value.lstrip("\ufeff").strip()
    for _ in range(3):
        previous = candidate
        fenced = _PLACEHOLDER_FENCE.fullmatch(candidate)
        if fenced is not None:
            candidate = fenced.group(1).strip()
        block_comment = _PLACEHOLDER_BLOCK_COMMENT.fullmatch(candidate)
        if block_comment is not None:
            candidate = next(
                group for group in block_comment.groups() if group is not None
            ).strip()
        line_comment = _PLACEHOLDER_LINE_COMMENT.fullmatch(candidate)
        if line_comment is not None:
            candidate = line_comment.group(1).strip()
        if len(candidate) >= 2 and (candidate[0], candidate[-1]) in {
            ('"', '"'),
            ("'", "'"),
            ("`", "`"),
        }:
            candidate = candidate[1:-1].strip()
        if candidate == previous:
            break
    return " ".join(candidate.casefold().split()).rstrip(".!;")


def _is_known_placeholder_text(value: str) -> bool:
    normalized = _unwrap_placeholder_text(value)
    if not normalized:
        return False
    if normalized in _EXACT_PLACEHOLDER_TEXT:
        return True
    return _QUALIFIED_PLACEHOLDER_TEXT.fullmatch(normalized) is not None


def inspect_public_deliverable(path: str) -> PublicDeliverableInspection:
    """Return whether ``path`` contains a minimally inspectable candidate.

    This deliberately does not judge correctness, media type, syntax, or
    minimum answer length.  A non-empty file is rejected only when it can be
    proven blank or a whole-file placeholder using bounded local inspection.
    """

    try:
        stat_result = os.stat(path)
    except (OSError, TypeError, ValueError):
        return PublicDeliverableInspection(
            exists=False,
            candidate_eligible=False,
            rejection_reason="missing",
        )
    if not stat_module.S_ISREG(stat_result.st_mode):
        return PublicDeliverableInspection(
            exists=False,
            candidate_eligible=False,
            rejection_reason="missing",
        )
    if stat_result.st_size == 0:
        return PublicDeliverableInspection(
            exists=True,
            candidate_eligible=False,
            rejection_reason="blank",
        )
    # Inspection is a narrow anti-placeholder guard, not a new validity gate.
    # Preserve prior behavior when a non-empty file cannot be inspected within
    # a small fixed budget.
    if stat_result.st_size > _MAX_PLACEHOLDER_INSPECTION_BYTES:
        return PublicDeliverableInspection(exists=True, candidate_eligible=True)

    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat_module.S_ISREG(before.st_mode) or before.st_size == 0:
                return PublicDeliverableInspection(
                    exists=stat_module.S_ISREG(before.st_mode),
                    candidate_eligible=False,
                    rejection_reason=(
                        "blank" if stat_module.S_ISREG(before.st_mode) else "missing"
                    ),
                )
            content = handle.read(_MAX_PLACEHOLDER_INSPECTION_BYTES + 1)
            after = os.fstat(handle.fileno())
        current = os.stat(path)
    except OSError:
        return PublicDeliverableInspection(exists=True, candidate_eligible=True)

    stable_identity = (
        (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        == (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        == (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
        )
    )
    if not stable_identity or len(content) != before.st_size:
        return PublicDeliverableInspection(exists=True, candidate_eligible=True)
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return PublicDeliverableInspection(exists=True, candidate_eligible=True)
    if not text.strip():
        return PublicDeliverableInspection(
            exists=True,
            candidate_eligible=False,
            rejection_reason="blank",
        )
    if _is_known_placeholder_text(text):
        return PublicDeliverableInspection(
            exists=True,
            candidate_eligible=False,
            rejection_reason="placeholder",
        )
    return PublicDeliverableInspection(exists=True, candidate_eligible=True)


__all__ = ["PublicDeliverableInspection", "inspect_public_deliverable"]
