"""Bounded target-package inventory and source helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.replay import _is_replayable_user_task_case
from aworld.self_evolve.targets import SelfEvolveTarget


def _target_runtime_skill_path(target: SelfEvolveTarget) -> Path | None:
    runtime_path = getattr(target, "runtime_skill_path", None)
    if runtime_path is not None:
        return Path(runtime_path).resolve()
    return Path(target.identity.path).resolve() if target.identity.path else None


def _replayable_user_task_dataset(dataset: SelfEvolveDataset) -> SelfEvolveDataset:
    return SelfEvolveDataset(
        cases=tuple(
            case for case in dataset.cases if _is_replayable_user_task_case(case)
        ),
        recipe=dataset.recipe,
    )


def _target_package_inventory(target: SelfEvolveTarget) -> tuple[str, ...]:
    target_path = _target_runtime_skill_path(target)
    if target_path is None or not target_path.exists():
        return ()
    root = target_path.parent
    return tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
    )


def _target_package_sources(
    target: SelfEvolveTarget,
    *,
    inventory: Sequence[str],
    max_file_chars: int = 128_000,
    max_total_chars: int = 512_000,
) -> dict[str, Mapping[str, object]]:
    """Load a bounded source inventory for later focused-repair closure.

    The mapping remains private until a conformance contract names a required
    branch path. Binary, oversized, symlinked, and out-of-package files are
    excluded so focused repair cannot broaden its mutation surface implicitly.
    """

    target_path = _target_runtime_skill_path(target)
    if target_path is None or not target_path.exists():
        return {}
    root = target_path.parent.resolve()
    remaining_chars = max_total_chars
    sources: dict[str, Mapping[str, object]] = {}
    for relative_path in inventory:
        if remaining_chars <= 0:
            break
        candidate = root.joinpath(*Path(relative_path).parts)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if (
            not resolved.is_relative_to(root)
            or candidate.is_symlink()
            or not resolved.is_file()
        ):
            continue
        try:
            if resolved.stat().st_size > max_file_chars * 4:
                continue
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if len(content) > max_file_chars or len(content) > remaining_chars:
            continue
        sources[relative_path] = {
            "content": content,
            "executable": bool(resolved.stat().st_mode & 0o111),
        }
        remaining_chars -= len(content)
    return sources


def _safe_artifact_name(value: str) -> str:
    readable = (
        "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in value
        ).strip("-")[:48]
        or "case"
    )
    suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{readable}-{suffix}"


def _stable_json_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
