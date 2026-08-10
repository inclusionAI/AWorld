# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Executable evidence-lifecycle policy for self-evolve replay tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping


REPLAY_EVIDENCE_POLICY_SCHEMA_VERSION = "aworld.replay.evidence_policy.v1"
_CONTROL_FILES = frozenset(
    {
        "evidence_manifest.jsonl",
        "evidence_bundle.json",
        "execution_request.json",
        "framework_evidence_policy.jsonl",
        "framework_evidence_state.json",
        "metrics.json",
        "trajectory.json",
        "stdout.txt",
        "stderr.txt",
    }
)
_MANIFEST_PAYLOAD_KEYS = frozenset(
    {
        "excerpt",
        "excerpts",
        "bounded_excerpt",
        "bounded_excerpts",
        "field_list",
        "fields",
        "fields_extracted",
        "key_fields",
        "claims_supported",
        "claims_supported_by",
        "summary",
        "structured_summary",
        "metadata",
    }
)


def enforce_replay_evidence_runtime_policy(
    tool_name: str,
    actions: Iterable[Any],
    message: Any,
) -> str | None:
    """Enforce replay evidence collection and return a violation code.

    The policy is opt-in for self-evolve replay subprocesses. It operates on
    bounded lifecycle metadata only; tool arguments and evidence payloads are
    never persisted in its state or violation records.
    """

    if os.environ.get("AWORLD_REPLAY_EVIDENCE_POLICY") != "1":
        return None
    artifact_root_value = os.environ.get(
        "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"
    )
    manifest_value = os.environ.get("AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST")
    if not artifact_root_value or not manifest_value:
        return None
    action_items = tuple(actions or ())
    artifact_root = Path(artifact_root_value)
    manifest_path = Path(manifest_value)
    artifact_root.mkdir(parents=True, exist_ok=True)
    manifest_entry_count = _valid_manifest_entry_count(
        manifest_path,
        artifact_root=artifact_root,
    )
    artifact_file_count, artifact_bytes = _artifact_inventory(artifact_root)
    artifact_file_limit = _positive_limit(
        "AWORLD_REPLAY_ARTIFACT_FILE_LIMIT",
        default=8,
    )
    artifact_byte_limit = _positive_limit(
        "AWORLD_REPLAY_ARTIFACT_BYTE_LIMIT",
        default=2_000_000,
    )
    owner = getattr(message, "context", None) or message
    attempt_count = int(
        getattr(owner, "_aworld_replay_evidence_policy_attempt_count", 0) or 0
    ) + len(action_items)
    setattr(
        owner,
        "_aworld_replay_evidence_policy_attempt_count",
        attempt_count,
    )
    phase = "evidence_ready" if manifest_entry_count else "collecting"
    state = {
        "schema_version": REPLAY_EVIDENCE_POLICY_SCHEMA_VERSION,
        "enforcement": "tool_boundary",
        "phase": phase,
        "tool_call_attempt_count": attempt_count,
        "manifest_entry_count": manifest_entry_count,
        "artifact_file_count": artifact_file_count,
        "artifact_bytes": artifact_bytes,
        "artifact_file_limit": artifact_file_limit,
        "artifact_byte_limit": artifact_byte_limit,
    }
    _write_state(artifact_root, state)

    violation_code: str | None = None
    if manifest_entry_count:
        violation_code = "tool_call_after_evidence_ready"
    elif artifact_file_count >= artifact_file_limit:
        violation_code = "artifact_file_limit_exhausted"
    elif artifact_bytes >= artifact_byte_limit:
        violation_code = "artifact_byte_limit_exhausted"
    if violation_code is None:
        return None

    first_action = action_items[0] if action_items else None
    violation = {
        **state,
        "code": violation_code,
        "tool_name": str(tool_name or "unknown")[:128],
        "action_name": str(
            getattr(first_action, "action_name", None) or "unknown"
        )[:128],
        "required_transition": (
            "finalize_task_response"
            if phase == "evidence_ready"
            else "persist_bounded_evidence_or_reduce_collection"
        ),
    }
    _append_violation(artifact_root, violation)
    return violation_code


def _positive_limit(name: str, *, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _valid_manifest_entry_count(
    manifest_path: Path,
    *,
    artifact_root: Path,
) -> int:
    try:
        raw = manifest_path.read_bytes()[:131_072].decode(
            "utf-8", errors="replace"
        )
    except OSError:
        return 0
    count = 0
    root = artifact_root.resolve(strict=False)
    for line in raw.splitlines()[:32]:
        try:
            entry = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(entry, Mapping):
            continue
        if not str(entry.get("source_id") or "").strip():
            continue
        if not str(entry.get("extraction_method") or "").strip():
            continue
        if not any(entry.get(key) for key in _MANIFEST_PAYLOAD_KEYS):
            continue
        artifact_path = entry.get("artifact_path")
        if isinstance(artifact_path, str) and artifact_path.strip():
            candidate = Path(artifact_path).expanduser()
            if not candidate.is_absolute():
                candidate = artifact_root / candidate
            resolved = candidate.resolve(strict=False)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                continue
        count += 1
    return count


def _artifact_inventory(artifact_root: Path) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0
    for current_root, directories, filenames in os.walk(
        artifact_root,
        followlinks=False,
    ):
        directories[:] = [
            name
            for name in directories[:64]
            if name != "logs" and not (Path(current_root) / name).is_symlink()
        ]
        for name in filenames[:256]:
            if name in _CONTROL_FILES:
                continue
            path = Path(current_root) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            file_count += 1
            total_bytes += max(0, size)
            if file_count >= 256:
                return file_count, total_bytes
    return file_count, total_bytes


def _write_state(
    artifact_root: Path,
    state: Mapping[str, Any],
) -> None:
    try:
        (artifact_root / "framework_evidence_state.json").write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        pass


def _append_violation(
    artifact_root: Path,
    violation: Mapping[str, Any],
) -> None:
    try:
        with (artifact_root / "framework_evidence_policy.jsonl").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(json.dumps(violation, ensure_ascii=False, sort_keys=True))
            stream.write("\n")
    except OSError:
        pass
