from __future__ import annotations

from pathlib import Path
from typing import Iterable


def prioritize_repo_aworld_path(sys_path: list[str], package_file: str) -> list[str]:
    """Ensure the sibling repo root is searched before other editable aworld installs."""
    package_path = Path(package_file).resolve()
    repo_root = package_path.parents[3]
    sibling_aworld = repo_root / "aworld"

    if not sibling_aworld.is_dir():
        return sys_path

    repo_root_str = str(repo_root)
    normalized = [path for path in sys_path if path != repo_root_str]
    return [repo_root_str, *normalized]


def bootstrap_aworld_repo_path(sys_path: list[str], package_file: str) -> None:
    updated = prioritize_repo_aworld_path(sys_path, package_file)
    sys_path[:] = updated
