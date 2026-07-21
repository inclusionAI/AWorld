from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from aworld.self_evolve.candidate_package import (
    candidate_package_fingerprint,
    validate_candidate_files,
)
from aworld.self_evolve.replay_capability import fingerprint_skill_package
from aworld.self_evolve.types import CandidateVariant, to_json_dict


@dataclass(frozen=True)
class SkillOverlayArtifact:
    run_id: str
    candidate_id: str
    shadow_root: Path
    candidate_skill_path: Path
    metadata_path: Path
    baseline_skill_roots: tuple[str, ...]
    candidate_skill_package_fingerprint: str


def create_candidate_skill_overlay(
    *,
    workspace_root: str | Path,
    run_id: str,
    candidate: CandidateVariant,
    target_skill_path: str | Path,
    baseline_skill_roots: Iterable[str | Path] = (),
) -> SkillOverlayArtifact:
    if candidate.target.target_type != "skill":
        raise ValueError("candidate skill overlay requires a skill target")

    workspace = Path(workspace_root)
    target_path = Path(target_skill_path).resolve()
    target_name = candidate.target.target_id
    inferred_root = target_path.parent.parent
    roots = tuple(Path(root).resolve() for root in baseline_skill_roots) or (inferred_root,)
    shadow_root = (
        workspace
        / ".aworld"
        / "self_evolve"
        / _safe_path(run_id)
        / "overlays"
        / _safe_path(candidate.candidate_id)
        / "skills"
    )
    if shadow_root.exists():
        shutil.rmtree(shadow_root)
    shadow_root.mkdir(parents=True, exist_ok=True)

    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for skill_dir in sorted(item for item in root.iterdir() if item.is_dir()):
            skill_file = _skill_file(skill_dir)
            if skill_file is None:
                continue
            if skill_dir.name == target_name:
                continue
            shutil.copytree(
                skill_dir,
                shadow_root / skill_dir.name,
                symlinks=True,
                dirs_exist_ok=True,
            )

    candidate_dir = shadow_root / target_name
    source_skill_dir = target_path.parent
    if target_path.is_file() and source_skill_dir.is_dir():
        shutil.copytree(
            source_skill_dir,
            candidate_dir,
            symlinks=True,
            dirs_exist_ok=True,
        )
    else:
        candidate_dir.mkdir(parents=True, exist_ok=True)
    candidate_skill_path = candidate_dir / "SKILL.md"
    candidate_skill_path.write_text(candidate.content, encoding="utf-8")
    applied_files = _apply_candidate_files(candidate_dir, candidate)
    candidate_skill_package_fingerprint = fingerprint_skill_package(candidate_dir)

    metadata_path = shadow_root.parent / "overlay.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "run_id": run_id,
        "candidate_id": candidate.candidate_id,
        "target": to_json_dict(candidate.target),
        "target_fingerprint": candidate.target_fingerprint,
        "candidate_package_fingerprint": candidate_package_fingerprint(candidate),
        "candidate_skill_package_fingerprint": candidate_skill_package_fingerprint,
        "target_skill_path": str(target_path),
        "candidate_skill_path": str(candidate_skill_path),
        "candidate_files": applied_files,
        "shadow_root": str(shadow_root),
        "baseline_skill_roots": [str(root) for root in roots],
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return SkillOverlayArtifact(
        run_id=run_id,
        candidate_id=candidate.candidate_id,
        shadow_root=shadow_root,
        candidate_skill_path=candidate_skill_path,
        metadata_path=metadata_path,
        baseline_skill_roots=tuple(str(root) for root in roots),
        candidate_skill_package_fingerprint=candidate_skill_package_fingerprint,
    )


def cleanup_self_evolve_overlays(
    workspace_root: str | Path,
    *,
    keep_latest_runs: int = 5,
) -> dict[str, object]:
    if keep_latest_runs < 0:
        raise ValueError("keep_latest_runs must be non-negative")
    root = Path(workspace_root) / ".aworld" / "self_evolve"
    if not root.exists():
        return {"removed_run_count": 0, "removed_paths": []}

    run_dirs = [
        path for path in root.iterdir()
        if path.is_dir() and (path / "overlays").exists()
    ]
    run_dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    removed_paths: list[str] = []
    for run_dir in run_dirs[keep_latest_runs:]:
        overlay_dir = run_dir / "overlays"
        shutil.rmtree(overlay_dir)
        removed_paths.append(str(overlay_dir))
    return {
        "removed_run_count": len(removed_paths),
        "removed_paths": removed_paths,
        "keep_latest_runs": keep_latest_runs,
    }


def _skill_file(skill_dir: Path) -> Path | None:
    for name in ("SKILL.md", "skill.md"):
        path = skill_dir / name
        if path.exists() and path.is_file():
            return path
    return None


def _apply_candidate_files(
    candidate_dir: Path,
    candidate: CandidateVariant,
) -> list[dict[str, object]]:
    applied: list[dict[str, object]] = []
    for item in validate_candidate_files(candidate.files):
        destination = candidate_dir.joinpath(*Path(item.path).parts)
        _assert_overlay_destination(candidate_dir, destination)
        if item.operation == "delete":
            if destination.is_dir() and not destination.is_symlink():
                raise ValueError(
                    f"candidate file delete cannot remove a directory: {item.path}"
                )
            destination.unlink(missing_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            _assert_overlay_destination(candidate_dir, destination)
            destination.write_text(item.content or "", encoding="utf-8")
            mode = destination.stat().st_mode
            destination.chmod((mode | 0o111) if item.executable else (mode & ~0o111))
        applied.append(
            {
                "path": item.path,
                "operation": item.operation,
                "executable": item.executable,
            }
        )
    return applied


def _assert_overlay_destination(root: Path, destination: Path) -> None:
    try:
        relative = destination.relative_to(root)
    except ValueError as exc:
        raise ValueError("candidate file destination escapes skill overlay") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(
                "candidate file destination cannot traverse a symlink: "
                f"{relative.as_posix()}"
            )


def _safe_path(value: str) -> str:
    safe = "".join(
        character
        for character in value
        if character.isalnum() or character in {"-", "_", "."}
    ).strip(".")
    if not safe:
        raise ValueError(f"invalid path component: {value!r}")
    return safe
