"""Bounded owner loader for global, workspace, nested, and path instructions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Iterable

from aworld.core.context.compiler import (
    AdapterDiagnostic,
    AdapterDiagnosticSeverity,
    AdapterResult,
    Authority,
    ContextItem,
    ContextKind,
    ContextScope,
    ContextSource,
    Lifetime,
    ScopeKind,
    SourceKind,
    Stability,
    Trust,
)


@dataclass(frozen=True, slots=True)
class ScopedInstructionLoaderConfig:
    max_file_bytes: int = 65536
    max_files: int = 32
    allow_imports: bool = True


class ScopedInstructionLoader:
    """Filesystem owner; final compiler receives only immutable Context items."""

    def __init__(self, config: ScopedInstructionLoaderConfig | None = None):
        self.config = config or ScopedInstructionLoaderConfig()

    @staticmethod
    def _frontmatter(text: str) -> tuple[str | None, str]:
        if not text.startswith("---\n"):
            return None, text
        end = text.find("\n---\n", 4)
        if end < 0:
            return None, text
        header = text[4:end]
        pattern = None
        for line in header.splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip() in {"path", "paths", "scope"}:
                pattern = value.strip().strip("'\"") or None
        return pattern, text[end + 5 :]

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _discover(self, workspace: Path, active_path: Path) -> tuple[Path, ...]:
        candidates = [workspace / ".aworld" / "AWORLD.md", workspace / "AWORLD.md"]
        directory = active_path if active_path.is_dir() else active_path.parent
        if self._within(directory, workspace):
            relative = directory.relative_to(workspace)
            cursor = workspace
            for part in relative.parts:
                cursor = cursor / part
                candidates.extend((cursor / ".aworld" / "AWORLD.md", cursor / "AWORLD.md"))
        seen: set[Path] = set()
        discovered: list[Path] = []
        for path in candidates:
            resolved = path.resolve()
            if not path.is_file() or resolved in seen:
                continue
            seen.add(resolved)
            discovered.append(resolved)
        return tuple(discovered)

    def load(
        self,
        *,
        workspace: str | Path,
        active_path: str | Path,
        task_epoch: int,
        global_instruction: str | Path | None = None,
    ) -> AdapterResult:
        workspace_path = Path(workspace).resolve()
        active = Path(active_path).resolve()
        roots = [workspace_path]
        initial: list[tuple[Path, Authority]] = []
        if global_instruction is not None:
            global_path = Path(global_instruction).resolve()
            roots.append(global_path.parent)
            if global_path.is_file():
                initial.append((global_path, Authority.WORKSPACE))
        initial.extend(
            (path, Authority.WORKSPACE if path.parent in {workspace_path, workspace_path / ".aworld"} else Authority.DIRECTORY)
            for path in self._discover(workspace_path, active)
        )
        items: list[ContextItem] = []
        diagnostics: list[AdapterDiagnostic] = []
        queue = list(initial)
        visited: set[Path] = set()
        while queue:
            path, authority = queue.pop(0)
            if path in visited:
                continue
            if len(visited) >= self.config.max_files:
                raise ValueError("scoped instruction file limit exceeded")
            if not any(self._within(path, root) for root in roots):
                raise ValueError("instruction import escapes its allowed root")
            size = path.stat().st_size
            if size > self.config.max_file_bytes:
                raise ValueError("scoped instruction file size limit exceeded")
            text = path.read_text(encoding="utf-8")
            source_hash = f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
            visited.add(path)
            pattern, content = self._frontmatter(text)
            import_lines: list[str] = []
            retained_lines: list[str] = []
            for line in content.splitlines():
                match = re.fullmatch(r"\s*@import\s+(.+?)\s*", line)
                if match and self.config.allow_imports:
                    import_lines.append(match.group(1).strip("'\""))
                else:
                    retained_lines.append(line)
            for relative_import in import_lines:
                imported = (path.parent / relative_import).resolve()
                if imported in visited:
                    diagnostics.append(
                        AdapterDiagnostic(
                            code="instruction_import_cycle_or_duplicate",
                            message="An instruction import was already visited.",
                            severity=AdapterDiagnosticSeverity.INFO,
                            source_identity=str(path),
                        )
                    )
                    continue
                if not imported.is_file():
                    raise ValueError("instruction import does not resolve to a file")
                queue.append((imported, authority))
            body = "\n".join(retained_lines).strip()
            if not body:
                continue
            relative_parent = (
                path.parent.relative_to(workspace_path).as_posix()
                if self._within(path.parent, workspace_path)
                else None
            )
            is_global = not self._within(path, workspace_path)
            if is_global:
                scope = ContextScope(kinds=(ScopeKind.GLOBAL,))
            elif pattern:
                scope = ContextScope(
                    kinds=(ScopeKind.WORKSPACE, ScopeKind.PATH_PATTERN),
                    workspace_id=str(workspace_path),
                    path_pattern=pattern,
                )
            elif relative_parent not in {None, ".", ".aworld"}:
                scope = ContextScope(
                    kinds=(ScopeKind.WORKSPACE, ScopeKind.DIRECTORY),
                    workspace_id=str(workspace_path),
                    directory=str(path.parent),
                )
            else:
                scope = ContextScope(
                    kinds=(ScopeKind.WORKSPACE,), workspace_id=str(workspace_path)
                )
            items.append(
                ContextItem(
                    id=f"instruction:{path}",
                    kind=ContextKind.INSTRUCTION,
                    payload={"role": "system", "content": body},
                    task_epoch=None,
                    authority=authority,
                    scope=scope,
                    lifetime=(
                        Lifetime.INSTALLATION if is_global else Lifetime.WORKSPACE
                    ),
                    priority=len(path.parts),
                    required=False,
                    trust=Trust.USER_CONTROLLED,
                    stability=Stability.STABLE,
                    token_limit=self.config.max_file_bytes,
                    reducer=None,
                    source=ContextSource(
                        kind=SourceKind.WORKSPACE_FILE,
                        uri=str(path),
                        version="scoped-instruction-v1",
                        ref={"source_content_hash": source_hash},
                    ),
                    version="v1",
                    activation_reason="scoped_instruction_discovery",
                    occurrence=len(items),
                )
            )
        return AdapterResult(items=tuple(items), diagnostics=tuple(diagnostics))


__all__ = ["ScopedInstructionLoader", "ScopedInstructionLoaderConfig"]
