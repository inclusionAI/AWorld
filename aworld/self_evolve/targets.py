from __future__ import annotations

import difflib
import hashlib
import re
import shutil
import uuid
from pathlib import Path
from typing import Protocol

from aworld.self_evolve.atomic_fs import atomic_exchange_paths
from aworld.self_evolve.candidate_package import validate_candidate_files
from aworld.self_evolve.replay_capability import (
    discover_replay_capability,
    fingerprint_skill_package,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.types import CandidateVariant, SelfEvolveTargetRef


class SelfEvolveTarget(Protocol):
    @property
    def identity(self) -> SelfEvolveTargetRef:
        ...

    def load_current_content(self) -> str:
        ...

    def fingerprint_current_content(self) -> str:
        ...

    def render_candidate_diff(self, candidate_content: str) -> str:
        ...


class SkillTextTarget:
    def __init__(
        self,
        skill_path: str | Path,
        *,
        target_id: str | None = None,
        allow_auto_apply: bool = False,
    ) -> None:
        self.path = Path(skill_path)
        self.allow_auto_apply = allow_auto_apply
        self._target_id = target_id or self._read_skill_name() or self.path.parent.name
        self._rollback_content: str | None = None
        self._rollback_files: dict[str, tuple[bool, bytes | None, int | None]] = {}
        self._rollback_created_directories: set[Path] = set()
        self._package_swap_backup: Path | None = None
        self._package_swap_original_existed: bool | None = None

    @property
    def identity(self) -> SelfEvolveTargetRef:
        return SelfEvolveTargetRef(
            target_type="skill",
            target_id=self._target_id,
            path=str(self.path),
        )

    def load_current_content(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def fingerprint_current_content(self) -> str:
        root = self._skill_package_root()
        if root.is_dir() and not root.is_symlink():
            return fingerprint_skill_package(root)
        digest = hashlib.sha256(self.load_current_content().encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    def render_candidate_diff(self, candidate_content: str) -> str:
        current_lines = self.load_current_content().splitlines(keepends=True)
        candidate_lines = candidate_content.splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(
                current_lines,
                candidate_lines,
                fromfile=f"current/{self._target_id}/SKILL.md",
                tofile=f"candidate/{self._target_id}/SKILL.md",
            )
        )

    def preserve_proposal(
        self,
        store: FilesystemSelfEvolveStore,
        run_id: str,
        candidate: CandidateVariant,
    ) -> tuple[Path, Path]:
        proposal_path = store.write_candidate(run_id, candidate)
        diff_path = proposal_path.with_suffix(".diff")
        diff_path.write_text(self.render_candidate_diff(candidate.content), encoding="utf-8")
        return proposal_path, diff_path

    def apply_candidate(self, candidate_content: str) -> None:
        if not self.allow_auto_apply:
            raise PermissionError(f"target {self._target_id!r} is not allowlisted for auto apply")
        self._rollback_content = self.load_current_content()
        self.path.write_text(candidate_content, encoding="utf-8")

    def apply_candidate_variant(
        self,
        candidate: CandidateVariant,
        *,
        expected_package_fingerprint: str | None = None,
        verified_content: str | None = None,
    ) -> None:
        if not self.allow_auto_apply:
            raise PermissionError(
                f"target {self._target_id!r} is not allowlisted for auto apply"
            )
        files = validate_candidate_files(candidate.files)
        self._prepare_package_rollback()
        root = self._skill_package_root().absolute()
        if root.is_symlink() or (root.exists() and not root.is_dir()):
            raise ValueError("skill package root must be a regular directory")
        root.parent.mkdir(parents=True, exist_ok=True)
        nonce = uuid.uuid4().hex
        staging = root.parent / f".{root.name}.aworld-stage-{nonce}"
        local_backup = root.parent / f".{root.name}.aworld-backup-{nonce}"
        swapped = False
        try:
            if root.exists():
                shutil.copytree(root, staging, symlinks=True)
            else:
                staging.mkdir()
            skill_relative = self._skill_apply_path().absolute().relative_to(root)
            staged_skill = staging / skill_relative
            staged_skill.parent.mkdir(parents=True, exist_ok=True)
            self._assert_package_destination(staging, staged_skill)
            staged_skill.write_text(candidate.content, encoding="utf-8")
            for item in files:
                destination = staging.joinpath(*Path(item.path).parts)
                self._assert_package_destination(staging, destination)
                existed = destination.exists() or destination.is_symlink()
                if destination.is_symlink() or (
                    existed and not destination.is_file()
                ):
                    raise ValueError(
                        f"candidate package path must be a regular file: {item.path}"
                    )
                if item.operation == "delete":
                    destination.unlink(missing_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._assert_package_destination(staging, destination)
                destination.write_text(item.content or "", encoding="utf-8")
                mode = destination.stat().st_mode
                destination.chmod(
                    (mode | 0o111) if item.executable else (mode & ~0o111)
                )
            discover_replay_capability(staging)
            if expected_package_fingerprint is not None:
                applied_content = staged_skill.read_text(encoding="utf-8")
                staged_skill.write_text(
                    verified_content if verified_content is not None else candidate.content,
                    encoding="utf-8",
                )
                observed_package_fingerprint = fingerprint_skill_package(staging)
                staged_skill.write_text(applied_content, encoding="utf-8")
                if observed_package_fingerprint != expected_package_fingerprint:
                    raise ValueError(
                        "skill package changed after replay verification"
                    )
            original_existed = root.exists()
            if original_existed:
                atomic_exchange_paths(root, staging)
                swapped = True
                staging.rename(local_backup)
                self._package_swap_backup = local_backup
            else:
                staging.rename(root)
                self._package_swap_backup = None
            self._package_swap_original_existed = original_existed
        except Exception:
            restore_source = staging if staging.exists() else local_backup
            if swapped and root.exists() and restore_source.exists():
                atomic_exchange_paths(root, restore_source)
            if staging.exists():
                shutil.rmtree(staging)
            if local_backup.exists():
                shutil.rmtree(local_backup)
            self._clear_package_rollback_state()
            self._cancel_package_rollback()
            raise

    def rollback(self) -> None:
        if self._rollback_candidate_package():
            self._rollback_content = None
            return
        self._rollback_candidate_files()
        if self._rollback_content is None:
            return
        self.path.write_text(self._rollback_content, encoding="utf-8")
        self._rollback_content = None

    def _skill_package_root(self) -> Path:
        return self.path.parent

    def _skill_apply_path(self) -> Path:
        return self.path

    def _prepare_package_rollback(self) -> None:
        self._rollback_content = self.load_current_content()

    def _cancel_package_rollback(self) -> None:
        self._rollback_content = None

    def commit_candidate_variant(self) -> None:
        if self._package_swap_backup is not None:
            shutil.rmtree(self._package_swap_backup)
        self._clear_package_rollback_state()
        self._rollback_content = None

    def _rollback_candidate_package(self) -> bool:
        if self._package_swap_original_existed is None:
            return False
        root = self._skill_package_root().absolute()
        if self._package_swap_original_existed:
            if self._package_swap_backup is None or not self._package_swap_backup.exists():
                raise RuntimeError("skill package rollback backup is missing")
            if root.exists():
                atomic_exchange_paths(root, self._package_swap_backup)
                shutil.rmtree(self._package_swap_backup)
            else:
                self._package_swap_backup.rename(root)
        elif root.exists() or root.is_symlink():
            trash = root.parent / f".{root.name}.aworld-trash-{uuid.uuid4().hex}"
            root.rename(trash)
            if trash.is_symlink() or trash.is_file():
                trash.unlink()
            else:
                shutil.rmtree(trash)
        self._clear_package_rollback_state()
        return True

    def _clear_package_rollback_state(self) -> None:
        self._package_swap_backup = None
        self._package_swap_original_existed = None
        self._rollback_files = {}
        self._rollback_created_directories = set()

    def _create_package_parents(self, root: Path, parent: Path) -> None:
        missing: list[Path] = []
        current = parent
        while current != root and not current.exists():
            if not current.is_relative_to(root):
                raise ValueError("candidate package parent escapes skill root")
            missing.append(current)
            current = current.parent
        self._assert_package_destination(root, parent)
        parent.mkdir(parents=True, exist_ok=True)
        self._rollback_created_directories.update(missing)

    @staticmethod
    def _assert_package_destination(root: Path, destination: Path) -> None:
        try:
            relative = destination.relative_to(root)
        except ValueError as exc:
            raise ValueError("candidate package path escapes skill root") from exc
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(
                    "candidate package path cannot traverse a symlink: "
                    f"{relative.as_posix()}"
                )

    def _rollback_candidate_files(self) -> None:
        root = self._skill_package_root().resolve()
        for relative, (existed, content, mode) in reversed(
            tuple(self._rollback_files.items())
        ):
            destination = root.joinpath(*Path(relative).parts)
            if existed:
                destination.parent.mkdir(parents=True, exist_ok=True)
                assert content is not None and mode is not None
                destination.write_bytes(content)
                destination.chmod(mode)
            else:
                destination.unlink(missing_ok=True)
        for directory in sorted(
            self._rollback_created_directories,
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
        self._rollback_files = {}
        self._rollback_created_directories = set()

    def _read_skill_name(self) -> str | None:
        if not self.path.exists():
            return None
        content = self.path.read_text(encoding="utf-8")
        match = re.search(r"^---\s*\n(.*?)\n---\s*", content, flags=re.DOTALL)
        if match is None:
            return None
        name_match = re.search(r"^name:\s*([^\n]+)\s*$", match.group(1), flags=re.MULTILINE)
        if name_match is None:
            return None
        return name_match.group(1).strip().strip("'\"")


class DraftSkillTextTarget(SkillTextTarget):
    """Skill target for a not-yet-existing self-evolve draft."""

    def __init__(
        self,
        skill_path: str | Path,
        *,
        target_id: str,
        release_path: str | Path,
        allow_auto_apply: bool = False,
    ) -> None:
        super().__init__(
            skill_path,
            target_id=target_id,
            allow_auto_apply=allow_auto_apply,
        )
        self.release_path = Path(release_path)
        self._rollback_existed: bool | None = None

    @property
    def runtime_skill_path(self) -> Path:
        return self.release_path

    def load_current_content(self) -> str:
        if self.path.exists():
            return self.path.read_text(encoding="utf-8")
        return _draft_skill_skeleton(self._target_id)

    def apply_candidate(self, candidate_content: str) -> None:
        if not self.allow_auto_apply:
            raise PermissionError(f"target {self._target_id!r} is not allowlisted for auto apply")
        self._rollback_existed = self.release_path.exists()
        self._rollback_content = (
            self.release_path.read_text(encoding="utf-8") if self._rollback_existed else None
        )
        self.release_path.parent.mkdir(parents=True, exist_ok=True)
        self.release_path.write_text(candidate_content, encoding="utf-8")

    def rollback(self) -> None:
        if self._rollback_candidate_package():
            self._rollback_content = None
            self._rollback_existed = None
            return
        self._rollback_candidate_files()
        if self._rollback_existed is None:
            return
        if self._rollback_existed:
            assert self._rollback_content is not None
            self.release_path.write_text(self._rollback_content, encoding="utf-8")
        else:
            self.release_path.unlink(missing_ok=True)
            try:
                self.release_path.parent.rmdir()
            except OSError:
                pass
        self._rollback_content = None
        self._rollback_existed = None

    def _skill_package_root(self) -> Path:
        return self.release_path.parent

    def _skill_apply_path(self) -> Path:
        return self.release_path

    def _prepare_package_rollback(self) -> None:
        self._rollback_existed = self.release_path.exists()
        self._rollback_content = (
            self.release_path.read_text(encoding="utf-8")
            if self._rollback_existed
            else None
        )

    def _cancel_package_rollback(self) -> None:
        self._rollback_content = None
        self._rollback_existed = None

    def commit_candidate_variant(self) -> None:
        super().commit_candidate_variant()
        self._rollback_existed = None


def _draft_skill_skeleton(target_id: str) -> str:
    title = " ".join(part.capitalize() for part in re.split(r"[-_]+", target_id) if part)
    title = title or target_id
    return (
        "---\n"
        f"name: {target_id}\n"
        "description: Draft skill generated by self-evolve for trajectory-backed task handling.\n"
        "---\n"
        f"# {title}\n\n"
        "Use trajectory evidence to solve the task and prefer grounded observations over "
        "prior assumptions. Persist large or unknown-size sources before inspecting them, "
        "then work from bounded structured extracts with source locations. After the first "
        "successful structured extraction, immediately persist its artifact and evidence "
        "manifest entry before collecting anything else. Stop redundant collection once "
        "the requested output and sufficient verified evidence exist, and return a bounded "
        "answer with a concise claim-to-evidence ledger. Record a failed tool path before "
        "switching once to a materially different bounded strategy.\n"
    )


class _SkeletonTextTarget:
    target_type = "text"

    def __init__(self, target_id: str, *, path: str | Path | None = None) -> None:
        self._target_id = target_id
        self.path = Path(path) if path is not None else None

    @property
    def identity(self) -> SelfEvolveTargetRef:
        return SelfEvolveTargetRef(
            target_type=self.target_type,
            target_id=self._target_id,
            path=str(self.path) if self.path is not None else None,
        )

    def load_current_content(self) -> str:
        raise NotImplementedError(f"{type(self).__name__} is a phase-1 skeleton target")

    def fingerprint_current_content(self) -> str:
        raise NotImplementedError(f"{type(self).__name__} is a phase-1 skeleton target")

    def render_candidate_diff(self, candidate_content: str) -> str:
        raise NotImplementedError(f"{type(self).__name__} is a phase-1 skeleton target")


class PromptSectionTarget(_SkeletonTextTarget):
    target_type = "prompt-section"


class ToolDescriptionTarget(_SkeletonTextTarget):
    target_type = "tool-description"


class AgentConfigTarget(_SkeletonTextTarget):
    target_type = "config"


class WorkspaceArtifactTarget(_SkeletonTextTarget):
    target_type = "workspace-artifact"
    _PROTECTED_ROOTS = {"aworld", "aworld-cli", "aworld_gateway"}
    _PROTECTED_FILES = {"setup.py", "pyproject.toml", "requirements.txt"}

    def __init__(
        self,
        path: str | Path,
        *,
        workspace_root: str | Path,
        target_id: str | None = None,
    ) -> None:
        artifact_path = Path(path).resolve()
        workspace_path = Path(workspace_root).resolve()
        relative = artifact_path.relative_to(workspace_path)
        if relative.parts and relative.parts[0] in self._PROTECTED_ROOTS:
            raise ValueError(f"protected product path cannot be a workspace artifact target: {path}")
        if relative.name in self._PROTECTED_FILES:
            raise ValueError(f"protected product path cannot be a workspace artifact target: {path}")
        super().__init__(target_id or relative.as_posix(), path=artifact_path)

    def load_current_content(self) -> str:
        if self.path is None:
            return ""
        return self.path.read_text(encoding="utf-8")

    def fingerprint_current_content(self) -> str:
        return "sha256:" + hashlib.sha256(self.load_current_content().encode("utf-8")).hexdigest()

    def render_candidate_diff(self, candidate_content: str) -> str:
        current_lines = self.load_current_content().splitlines(keepends=True)
        candidate_lines = candidate_content.splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(
                current_lines,
                candidate_lines,
                fromfile=f"current/{self._target_id}",
                tofile=f"candidate/{self._target_id}",
            )
        )

    def preserve_proposal(
        self,
        store: FilesystemSelfEvolveStore,
        run_id: str,
        candidate: CandidateVariant,
    ) -> tuple[Path, Path]:
        proposal_path = store.write_candidate(run_id, candidate)
        diff_path = proposal_path.with_suffix(".diff")
        diff_path.write_text(self.render_candidate_diff(candidate.content), encoding="utf-8")
        return proposal_path, diff_path
