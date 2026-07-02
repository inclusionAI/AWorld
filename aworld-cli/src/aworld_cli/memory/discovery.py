from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

IMPORT_PATTERN = re.compile(r"^@(?:import\s+)?(.+\.md)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class WorkspaceInstructionLayers:
    global_file: Path | None
    workspace_file: Path | None
    compatibility_file: Path | None
    canonical_write_file: Path
    effective_read_files: tuple[Path, ...]
    warning: str | None = None


def discover_workspace_instruction_layers(
    workspace_path: str | os.PathLike[str] | None = None,
) -> WorkspaceInstructionLayers:
    workspace = Path(workspace_path or os.getcwd()).expanduser().resolve()
    global_file = workspace_file = compatibility_file = None

    global_candidate = (Path.home() / ".aworld" / "AWORLD.md").resolve()
    if global_candidate.is_file():
        global_file = global_candidate

    workspace_candidate = workspace / ".aworld" / "AWORLD.md"
    if workspace_candidate.is_file():
        workspace_file = workspace_candidate.resolve()

    compatibility_candidate = workspace / "AWORLD.md"
    if compatibility_candidate.is_file():
        compatibility_file = compatibility_candidate.resolve()

    warning = None
    if workspace_file is None and compatibility_file is not None:
        warning = (
            f"Reading compatibility workspace instructions from {compatibility_file}. "
            f"Move edits to {workspace_candidate.resolve()}."
        )

    effective_read_files = tuple(
        path
        for path in (
            global_file,
            workspace_file if workspace_file is not None else compatibility_file,
        )
        if path is not None
    )

    return WorkspaceInstructionLayers(
        global_file=global_file,
        workspace_file=workspace_file,
        compatibility_file=compatibility_file,
        canonical_write_file=workspace_candidate.resolve(),
        effective_read_files=effective_read_files,
        warning=warning,
    )


def load_instruction_text(file_path: str | os.PathLike[str], visited: set[str] | None = None) -> str:
    path = Path(file_path).expanduser().resolve()
    seen = set() if visited is None else set(visited)

    resolved = str(path)
    if resolved in seen:
        return f"\n<!-- Circular import: {path} -->\n"
    seen.add(resolved)

    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return f"\n<!-- Error reading file: {path} -->\n"

    def replace_import(match: re.Match[str]) -> str:
        import_value = match.group(1).strip()
        nested_path = _resolve_import_path(import_value, base_path=path)
        if nested_path is None:
            return f"\n<!-- Import not found: {import_value} -->\n"
        return load_instruction_text(nested_path, visited=seen)

    return IMPORT_PATTERN.sub(replace_import, content)


def _resolve_import_path(import_path: str, base_path: Path) -> Path | None:
    candidate = Path(import_path).expanduser()
    if not candidate.is_absolute():
        candidate = base_path.parent / candidate

    resolved = candidate.resolve()
    if resolved.is_file():
        return resolved
    return None
