from __future__ import annotations

from pathlib import Path

from aworld.evaluations.substrate import (
    list_eval_suites,
    list_matching_eval_suites,
    load_declared_eval_suites,
    resolve_eval_suite_selection,
)


def resolve_cli_target_path(target: str) -> Path:
    target_path = Path(target).expanduser().resolve()
    if not target_path.exists():
        raise FileNotFoundError(f"evaluation target does not exist: {target_path}")
    return target_path


def discover_workspace_suites(target: str | None = None) -> list[str]:
    if target is None:
        load_declared_eval_suites()
        return list_eval_suites()
    target_path = resolve_cli_target_path(target)
    load_declared_eval_suites(target_path.parent if target_path.is_file() else target_path)
    return list_matching_eval_suites(target_path)


def resolve_workspace_suite_selection(
    *,
    target: str,
    suite: str | None = None,
) -> dict[str, str | None]:
    target_path = resolve_cli_target_path(target)
    load_declared_eval_suites(target_path.parent if target_path.is_file() else target_path)
    selection = resolve_eval_suite_selection(suite, target_path)
    return {
        "requested": suite,
        "resolved": selection.suite_id,
        "mode": selection.mode,
    }
