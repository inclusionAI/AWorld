"""Deterministic scope and lifetime matching for Context compilation."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from .models import ContextItem, ContextScope, Lifetime, ScopeKind


def _clean_path(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(PurePosixPath(value.replace("\\", "/")))
    return cleaned.rstrip("/") or "/"


@dataclass(frozen=True, slots=True)
class ContextResolutionTarget:
    """Capability-free selectors for one final compile invocation."""

    workspace_id: str | None = None
    directory: str | None = None
    active_paths: tuple[str, ...] = ()
    session_id: str | None = None
    task_id: str | None = None
    turn_id: str | None = None
    agent_id: str | None = None
    child_task_id: str | None = None
    task_epoch: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "workspace_id", "session_id", "task_id", "turn_id", "agent_id",
            "child_task_id",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string or None")
        object.__setattr__(self, "directory", _clean_path(self.directory))
        object.__setattr__(
            self,
            "active_paths",
            tuple(_clean_path(path) or "/" for path in self.active_paths),
        )
        if self.task_epoch is not None and (
            isinstance(self.task_epoch, bool)
            or not isinstance(self.task_epoch, int)
            or self.task_epoch < 0
        ):
            raise ValueError("task_epoch must be a non-negative integer or None")


def _directory_matches(required: str, target: ContextResolutionTarget) -> bool:
    required_path = _clean_path(required) or "/"
    paths = target.active_paths or ((target.directory,) if target.directory else ())
    return any(
        path == required_path or path.startswith(required_path.rstrip("/") + "/")
        for path in paths
    )


def _pattern_matches(pattern: str, target: ContextResolutionTarget) -> bool:
    paths = target.active_paths or ((target.directory,) if target.directory else ())
    normalized_pattern = pattern.replace("\\", "/")
    candidates: list[str] = []
    workspace = _clean_path(target.workspace_id)
    for path in paths:
        candidates.append(path)
        if workspace and (
            path == workspace or path.startswith(workspace.rstrip("/") + "/")
        ):
            relative = path[len(workspace) :].lstrip("/") or "."
            candidates.append(relative)
    return any(fnmatchcase(path, normalized_pattern) for path in candidates)


def scope_matches(scope: ContextScope, target: ContextResolutionTarget) -> bool:
    """Return true only when every declared selector matches the target."""
    if scope.kinds == (ScopeKind.UNKNOWN,):
        return False
    for kind in scope.kinds:
        if kind is ScopeKind.GLOBAL:
            continue
        if kind is ScopeKind.WORKSPACE and scope.workspace_id != target.workspace_id:
            return False
        if kind is ScopeKind.DIRECTORY and not _directory_matches(
            scope.directory or "", target
        ):
            return False
        if kind is ScopeKind.PATH_PATTERN and not _pattern_matches(
            scope.path_pattern or "", target
        ):
            return False
        selector = {
            ScopeKind.SESSION: (scope.session_id, target.session_id),
            ScopeKind.TASK: (scope.task_id, target.task_id),
            ScopeKind.TURN: (scope.turn_id, target.turn_id),
            ScopeKind.AGENT: (scope.agent_id, target.agent_id),
            ScopeKind.CHILD_TASK: (scope.child_task_id, target.child_task_id),
        }.get(kind)
        if selector is not None and selector[0] != selector[1]:
            return False
    return True


def lifetime_matches(item: ContextItem, target: ContextResolutionTarget) -> bool:
    """Apply lifecycle ownership independently from hierarchical scope."""
    if item.lifetime is Lifetime.UNKNOWN:
        return False
    if item.task_epoch is not None and item.task_epoch != target.task_epoch:
        return False
    if item.lifetime in {Lifetime.INSTALLATION, Lifetime.WORKSPACE}:
        return True
    if item.lifetime is Lifetime.SESSION:
        return target.session_id is not None
    if item.lifetime is Lifetime.TASK:
        return target.task_id is not None and target.task_epoch is not None
    if item.lifetime in {Lifetime.TURN, Lifetime.SINGLE_CALL}:
        return target.turn_id is not None
    return False


def scope_specificity(scope: ContextScope) -> int:
    weights = {
        ScopeKind.GLOBAL: 0,
        ScopeKind.WORKSPACE: 10,
        ScopeKind.DIRECTORY: 20,
        ScopeKind.PATH_PATTERN: 25,
        ScopeKind.SESSION: 30,
        ScopeKind.TASK: 40,
        ScopeKind.TURN: 50,
        ScopeKind.AGENT: 35,
        ScopeKind.CHILD_TASK: 45,
        ScopeKind.UNKNOWN: -1000,
    }
    return sum(weights[kind] for kind in scope.kinds)


__all__ = [
    "ContextResolutionTarget",
    "lifetime_matches",
    "scope_matches",
    "scope_specificity",
]
