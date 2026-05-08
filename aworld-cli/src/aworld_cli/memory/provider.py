from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aworld_cli.builtin_plugins.memory_cli.common import append_remembered_guidance
from aworld_cli.memory.durable import (
    INSTRUCTION_MEMORY_TYPES,
    DurableMemoryRecord,
    DurableMemoryWriteResult,
    append_durable_memory_record,
    read_all_durable_memory_records,
    read_durable_memory_records,
)
from aworld_cli.memory.discovery import (
    WorkspaceInstructionLayers,
    discover_workspace_instruction_layers,
    load_instruction_text,
)
from aworld_cli.memory.governance import (
    append_governed_review,
    list_governed_decisions as load_governed_decisions,
)
from aworld_cli.memory.relevance import (
    recall_relevant_durable_memory_texts,
    recall_relevant_session_log_texts,
)


@dataclass(frozen=True)
class InstructionContext:
    texts: tuple[str, ...]
    warning: str | None = None
    source_files: tuple[Path, ...] = ()


@dataclass(frozen=True)
class RelevantMemoryContext:
    texts: tuple[str, ...]
    source_files: tuple[Path, ...] = ()


@dataclass(frozen=True)
class ExplicitDurableWriteResult:
    record_path: Path
    memory_type: str
    record_created: bool
    instruction_target: Path | None = None
    instruction_updated: bool = False


class CliDurableMemoryProvider:
    def get_instruction_layers(
        self,
        workspace_path: str | Path | None = None,
    ) -> WorkspaceInstructionLayers:
        return discover_workspace_instruction_layers(workspace_path=workspace_path)

    def get_instruction_context(
        self,
        workspace_path: str | Path | None = None,
    ) -> InstructionContext:
        layers = self.get_instruction_layers(workspace_path=workspace_path)
        source_files = layers.effective_read_files

        return InstructionContext(
            texts=tuple(load_instruction_text(path) for path in source_files),
            warning=layers.warning,
            source_files=source_files,
        )

    def get_relevant_memory_context(
        self,
        workspace_path: str | Path | None = None,
        query: str = "",
        limit: int = 3,
    ) -> RelevantMemoryContext:
        durable_texts, durable_files = recall_relevant_durable_memory_texts(
            workspace_path=workspace_path,
            query=query,
            limit=limit,
        )
        session_texts, session_files = recall_relevant_session_log_texts(
            workspace_path=workspace_path,
            query=query,
            limit=limit,
        )
        merged = _merge_relevant_memory_entries(
            limit=limit,
            durable_texts=durable_texts,
            durable_files=durable_files,
            session_texts=session_texts,
            session_files=session_files,
        )
        return RelevantMemoryContext(texts=merged[0], source_files=merged[1])

    def get_durable_memory_records(
        self,
        workspace_path: str | Path,
        memory_type: str | None = None,
    ) -> tuple[DurableMemoryRecord, ...]:
        return read_all_durable_memory_records(
            workspace_path=workspace_path,
            memory_type=memory_type,
        )

    def list_governed_decisions(
        self,
        workspace_path: str | Path,
    ) -> tuple[dict, ...]:
        return tuple(load_governed_decisions(workspace_path))

    def record_governed_review(
        self,
        workspace_path: str | Path,
        *,
        decision_id: str,
        review_action: str,
    ) -> Path:
        return append_governed_review(
            workspace_path,
            {
                "decision_id": decision_id,
                "review_action": review_action,
            },
        )

    def get_active_durable_memory_records(
        self,
        workspace_path: str | Path,
        memory_type: str | None = None,
    ) -> tuple[DurableMemoryRecord, ...]:
        return read_durable_memory_records(
            workspace_path,
            memory_type=memory_type,
        )

    def append_durable_memory_record(
        self,
        workspace_path: str | Path,
        *,
        text: str,
        memory_type: str,
        source: str,
        memory_kind: str | None = None,
        decision_id: str | None = None,
        source_ref: dict[str, str] | None = None,
    ) -> ExplicitDurableWriteResult:
        write_result: DurableMemoryWriteResult = append_durable_memory_record(
            workspace_path=workspace_path,
            text=text,
            memory_type=memory_type,
            source=source,
            memory_kind=memory_kind,
            decision_id=decision_id,
            source_ref=source_ref,
        )

        instruction_target: Path | None = None
        instruction_updated = False
        if _is_instruction_eligible_memory(
            memory_type=write_result.memory_type,
            memory_kind=memory_kind,
        ):
            instruction_target, instruction_updated = append_remembered_guidance(
                workspace_path=workspace_path,
                text=text,
            )

        return ExplicitDurableWriteResult(
            record_path=write_result.record_path,
            memory_type=write_result.memory_type,
            record_created=write_result.record_created,
            instruction_target=instruction_target,
            instruction_updated=instruction_updated,
        )


def _is_instruction_eligible_memory(*, memory_type: str, memory_kind: str | None) -> bool:
    if memory_type not in INSTRUCTION_MEMORY_TYPES:
        return False
    if memory_kind is None:
        return True
    return memory_kind in {"preference", "constraint", "workflow"}


def _merge_relevant_memory_entries(
    *,
    limit: int,
    durable_texts: tuple[str, ...],
    durable_files: tuple[Path, ...],
    session_texts: tuple[str, ...],
    session_files: tuple[Path, ...],
) -> tuple[tuple[str, ...], tuple[Path, ...]]:
    selected_texts: list[str] = []
    selected_files: list[Path] = []

    for text, source_files in (
        (durable_texts, durable_files),
        (session_texts, session_files),
    ):
        for item in text:
            if item in selected_texts:
                continue
            if len(selected_texts) >= max(limit, 0):
                return tuple(selected_texts), tuple(selected_files)
            selected_texts.append(item)
            for source_file in source_files:
                if source_file not in selected_files:
                    selected_files.append(source_file)

    return tuple(selected_texts), tuple(selected_files)
