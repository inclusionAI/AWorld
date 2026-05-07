from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aworld_cli.builtin_plugins.memory_cli.common import append_remembered_guidance
from aworld_cli.memory.durable import (
    INSTRUCTION_MEMORY_TYPES,
    DurableMemoryRecord,
    DurableMemoryWriteResult,
    append_durable_memory_record,
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
from aworld_cli.memory.relevance import recall_relevant_session_log_texts


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
        texts, source_files = recall_relevant_session_log_texts(
            workspace_path=workspace_path,
            query=query,
            limit=limit,
        )
        return RelevantMemoryContext(texts=texts, source_files=source_files)

    def get_durable_memory_records(
        self,
        workspace_path: str | Path,
        memory_type: str | None = None,
    ) -> tuple[DurableMemoryRecord, ...]:
        return read_durable_memory_records(
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
        records = self.get_durable_memory_records(
            workspace_path,
            memory_type=memory_type,
        )
        reverted_governed_keys = self._reverted_governed_record_keys(workspace_path)
        if not reverted_governed_keys:
            return records

        active_records: list[DurableMemoryRecord] = []
        for record in records:
            if (
                record.source == "governed_auto_promotion"
                and record.decision_id
                and record.decision_id in reverted_governed_keys
            ):
                continue
            active_records.append(record)
        return tuple(active_records)

    def append_durable_memory_record(
        self,
        workspace_path: str | Path,
        *,
        text: str,
        memory_type: str,
        source: str,
        decision_id: str | None = None,
        source_ref: dict[str, str] | None = None,
    ) -> ExplicitDurableWriteResult:
        write_result: DurableMemoryWriteResult = append_durable_memory_record(
            workspace_path=workspace_path,
            text=text,
            memory_type=memory_type,
            source=source,
            decision_id=decision_id,
            source_ref=source_ref,
        )

        instruction_target: Path | None = None
        instruction_updated = False
        if write_result.memory_type in INSTRUCTION_MEMORY_TYPES:
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

    def _reverted_governed_record_keys(
        self,
        workspace_path: str | Path,
    ) -> set[str]:
        reverted_keys: set[str] = set()
        for decision in self.list_governed_decisions(workspace_path):
            if decision.get("decision") != "durable_memory":
                continue
            decision_id = str(decision.get("decision_id") or "").strip()
            if not decision_id:
                continue
            reviews = decision.get("reviews")
            if not isinstance(reviews, list):
                continue
            if any(review.get("review_action") == "reverted" for review in reviews):
                reverted_keys.add(decision_id)
        return reverted_keys
