from __future__ import annotations

import ast
import hashlib
import json
import os
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Mapping, Sequence


HANDBOOK_SCHEMA_VERSION = "aworld.self_evolve.handbook.v1"
HANDBOOK_SLICE_SCHEMA_VERSION = "aworld.self_evolve.handbook_slice.v1"
HANDBOOK_SOURCE_ROOT = Path("aworld/self_evolve")
MAX_HANDBOOK_SLICE_LOCATORS = 48

LOCATOR_ACTIVE = "active"
LOCATOR_FROZEN = "frozen"


class HandbookLocatorIntegrityError(RuntimeError):
    """Raised when automatic mutation would use unresolved source locations."""


@dataclass(frozen=True)
class SourceLocator:
    relative_path: str
    qualified_symbol: str
    symbol_kind: str
    start_line: int | None
    end_line: int | None
    module_fingerprint: str
    source_fingerprint: str | None
    status: str = LOCATOR_ACTIVE
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {LOCATOR_ACTIVE, LOCATOR_FROZEN}:
            raise ValueError(f"unsupported locator status: {self.status}")
        if not self.relative_path or Path(self.relative_path).is_absolute():
            raise ValueError("handbook locator path must be workspace-relative")
        if not self.qualified_symbol:
            raise ValueError("handbook locator requires a qualified symbol")
        if self.status == LOCATOR_ACTIVE and (
            self.start_line is None
            or self.end_line is None
            or self.source_fingerprint is None
        ):
            raise ValueError("active handbook locator requires a source span")

    @property
    def locator_id(self) -> str:
        return f"{self.relative_path}::{self.qualified_symbol}"

    def to_dict(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "qualified_symbol": self.qualified_symbol,
            "symbol_kind": self.symbol_kind,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "module_fingerprint": self.module_fingerprint,
            "source_fingerprint": self.source_fingerprint,
            "status": self.status,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SourceLocator":
        return cls(
            relative_path=str(value.get("relative_path") or ""),
            qualified_symbol=str(value.get("qualified_symbol") or ""),
            symbol_kind=str(value.get("symbol_kind") or "unknown"),
            start_line=_optional_int(value.get("start_line")),
            end_line=_optional_int(value.get("end_line")),
            module_fingerprint=str(value.get("module_fingerprint") or "missing"),
            source_fingerprint=(
                str(value["source_fingerprint"])
                if value.get("source_fingerprint") is not None
                else None
            ),
            status=str(value.get("status") or LOCATOR_FROZEN),
            error=(str(value["error"]) if value.get("error") is not None else None),
        )


@dataclass(frozen=True)
class HandbookModule:
    relative_path: str
    fingerprint: str
    locators: tuple[SourceLocator, ...]
    status: str = LOCATOR_ACTIVE
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "fingerprint": self.fingerprint,
            "status": self.status,
            "error": self.error,
            "locators": [item.to_dict() for item in self.locators],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HandbookModule":
        raw_locators = value.get("locators")
        return cls(
            relative_path=str(value.get("relative_path") or ""),
            fingerprint=str(value.get("fingerprint") or "missing"),
            locators=tuple(
                SourceLocator.from_dict(item)
                for item in raw_locators or ()
                if isinstance(item, Mapping)
            ),
            status=str(value.get("status") or LOCATOR_FROZEN),
            error=(str(value["error"]) if value.get("error") is not None else None),
        )


@dataclass(frozen=True)
class HandbookComponent:
    component_id: str
    responsibility: str
    module_paths: tuple[str, ...]
    entry_locators: tuple[SourceLocator, ...]
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "component_id": self.component_id,
            "responsibility": self.responsibility,
            "module_paths": list(self.module_paths),
            "entry_locators": [item.to_dict() for item in self.entry_locators],
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HandbookComponent":
        raw_locators = value.get("entry_locators")
        return cls(
            component_id=str(value.get("component_id") or ""),
            responsibility=str(value.get("responsibility") or ""),
            module_paths=_string_tuple(value.get("module_paths")),
            entry_locators=tuple(
                SourceLocator.from_dict(item)
                for item in raw_locators or ()
                if isinstance(item, Mapping)
            ),
            status=str(value.get("status") or LOCATOR_FROZEN),
        )


@dataclass(frozen=True)
class StateRegisterEntry:
    state_id: str
    description: str
    create: tuple[SourceLocator, ...]
    read: tuple[SourceLocator, ...]
    update: tuple[SourceLocator, ...]
    cleanup: tuple[SourceLocator, ...]
    retention: str
    status: str

    @property
    def locators(self) -> tuple[SourceLocator, ...]:
        return (*self.create, *self.read, *self.update, *self.cleanup)

    def to_dict(self) -> dict[str, object]:
        return {
            "state_id": self.state_id,
            "description": self.description,
            "create": [item.to_dict() for item in self.create],
            "read": [item.to_dict() for item in self.read],
            "update": [item.to_dict() for item in self.update],
            "cleanup": [item.to_dict() for item in self.cleanup],
            "retention": self.retention,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "StateRegisterEntry":
        def locators(key: str) -> tuple[SourceLocator, ...]:
            raw = value.get(key)
            return tuple(
                SourceLocator.from_dict(item)
                for item in raw or ()
                if isinstance(item, Mapping)
            )

        return cls(
            state_id=str(value.get("state_id") or ""),
            description=str(value.get("description") or ""),
            create=locators("create"),
            read=locators("read"),
            update=locators("update"),
            cleanup=locators("cleanup"),
            retention=str(value.get("retention") or "unspecified"),
            status=str(value.get("status") or LOCATOR_FROZEN),
        )


@dataclass(frozen=True)
class HandbookStage:
    stage_id: str
    component_id: str
    next_stages: tuple[str, ...]
    failure_exits: tuple[str, ...]
    entry_locator: SourceLocator
    terminal: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "stage_id": self.stage_id,
            "component_id": self.component_id,
            "next_stages": list(self.next_stages),
            "failure_exits": list(self.failure_exits),
            "entry_locator": self.entry_locator.to_dict(),
            "terminal": self.terminal,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HandbookStage":
        raw_locator = value.get("entry_locator")
        if not isinstance(raw_locator, Mapping):
            raise ValueError("handbook stage requires an entry locator")
        return cls(
            stage_id=str(value.get("stage_id") or ""),
            component_id=str(value.get("component_id") or ""),
            next_stages=_string_tuple(value.get("next_stages")),
            failure_exits=_string_tuple(value.get("failure_exits")),
            entry_locator=SourceLocator.from_dict(raw_locator),
            terminal=bool(value.get("terminal", False)),
        )


@dataclass(frozen=True)
class HandbookSnapshot:
    modules: tuple[HandbookModule, ...]
    components: tuple[HandbookComponent, ...]
    state_register: tuple[StateRegisterEntry, ...]
    stages: tuple[HandbookStage, ...]
    schema_version: str = HANDBOOK_SCHEMA_VERSION

    @property
    def frozen_locator_count(self) -> int:
        return sum(
            locator.status == LOCATOR_FROZEN
            for module in self.modules
            for locator in module.locators
        )

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self._payload())

    def _payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "modules": [item.to_dict() for item in self.modules],
            "components": [item.to_dict() for item in self.components],
            "state_register": [item.to_dict() for item in self.state_register],
            "stages": [item.to_dict() for item in self.stages],
        }

    def to_dict(self) -> dict[str, object]:
        payload = self._payload()
        payload.update(
            {
                "fingerprint": self.fingerprint,
                "frozen_locator_count": self.frozen_locator_count,
            }
        )
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HandbookSnapshot":
        if value.get("schema_version") != HANDBOOK_SCHEMA_VERSION:
            raise ValueError("unsupported handbook snapshot schema")
        snapshot = cls(
            modules=tuple(
                HandbookModule.from_dict(item)
                for item in value.get("modules") or ()
                if isinstance(item, Mapping)
            ),
            components=tuple(
                HandbookComponent.from_dict(item)
                for item in value.get("components") or ()
                if isinstance(item, Mapping)
            ),
            state_register=tuple(
                StateRegisterEntry.from_dict(item)
                for item in value.get("state_register") or ()
                if isinstance(item, Mapping)
            ),
            stages=tuple(
                HandbookStage.from_dict(item)
                for item in value.get("stages") or ()
                if isinstance(item, Mapping)
            ),
        )
        recorded = value.get("fingerprint")
        if recorded is not None and recorded != snapshot.fingerprint:
            raise ValueError("handbook snapshot fingerprint mismatch")
        return snapshot


@dataclass(frozen=True)
class HandbookSlice:
    target_path: str
    component_ids: tuple[str, ...]
    components: tuple[HandbookComponent, ...]
    state_register: tuple[StateRegisterEntry, ...]
    stages: tuple[HandbookStage, ...]
    frozen_locator_ids: tuple[str, ...]
    snapshot_fingerprint: str
    schema_version: str = HANDBOOK_SLICE_SCHEMA_VERSION

    @property
    def mutation_allowed(self) -> bool:
        return not self.frozen_locator_ids

    def to_prompt_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "target_path": self.target_path,
            "component_ids": list(self.component_ids),
            "mutation_allowed": self.mutation_allowed,
            "frozen_locator_ids": list(self.frozen_locator_ids),
            "components": [item.to_dict() for item in self.components],
            "state_register": [item.to_dict() for item in self.state_register],
            "stages": [item.to_dict() for item in self.stages],
        }


@dataclass(frozen=True)
class _ComponentSpec:
    component_id: str
    responsibility: str
    module_paths: tuple[str, ...]
    entries: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _StateSpec:
    state_id: str
    description: str
    create: tuple[tuple[str, str], ...]
    read: tuple[tuple[str, str], ...]
    update: tuple[tuple[str, str], ...]
    cleanup: tuple[tuple[str, str], ...]
    retention: str


@dataclass(frozen=True)
class _StageSpec:
    stage_id: str
    component_id: str
    next_stages: tuple[str, ...]
    entry: tuple[str, str]
    terminal: bool = False


_COMPONENT_SPECS = (
    _ComponentSpec(
        "ingestion",
        "Resolve source material into immutable dataset and evidence contracts.",
        ("aworld/self_evolve/datasets.py", "aworld/self_evolve/ingestion"),
        (
            ("aworld/self_evolve/datasets.py", "build_dataset_from_source"),
            ("aworld/self_evolve/ingestion/registry.py", "IngestionRegistry"),
        ),
    ),
    _ComponentSpec(
        "target_selection",
        "Resolve mutation intent, target ownership, provenance, and authorization.",
        (
            "aworld/self_evolve/credit_assignment.py",
            "aworld/self_evolve/provenance.py",
            "aworld/self_evolve/targets.py",
        ),
        (
            (
                "aworld/self_evolve/credit_assignment.py",
                "build_target_selection_decision",
            ),
            ("aworld/self_evolve/targets.py", "SkillTextTarget"),
        ),
    ),
    _ComponentSpec(
        "candidate_generation",
        "Compile focused evidence and produce bounded candidate packages.",
        (
            "aworld/self_evolve/candidate_generation.py",
            "aworld/self_evolve/optimizers",
            "aworld/self_evolve/population.py",
            "aworld/self_evolve/evolution_context.py",
        ),
        (
            ("aworld/self_evolve/candidate_generation.py", "CandidateGenerationAgent"),
            ("aworld/self_evolve/optimizers/base.py", "OptimizerRequest"),
            ("aworld/self_evolve/evolution_context.py", "compile_evolution_context"),
        ),
    ),
    _ComponentSpec(
        "conformance",
        "Validate package shape, mutation surface, repair contract, and local gates.",
        (
            "aworld/self_evolve/gates.py",
            "aworld/self_evolve/candidate_protocol.py",
            "aworld/self_evolve/candidate_package.py",
            "aworld/self_evolve/repair_conformance.py",
            "aworld/self_evolve/controllers/run_repair_conformance.py",
            "aworld/self_evolve/controllers/run_capability_validation.py",
        ),
        (
            ("aworld/self_evolve/gates.py", "CandidatePackageGate"),
            (
                "aworld/self_evolve/controllers/run_repair_conformance.py",
                "preflight_candidate_repair_conformance",
            ),
        ),
    ),
    _ComponentSpec(
        "replay",
        "Adapt dependencies and execute comparable baseline/candidate task rollouts.",
        (
            "aworld/self_evolve/replay.py",
            "aworld/self_evolve/replay_adaptation.py",
            "aworld/self_evolve/replay_capability.py",
            "aworld/self_evolve/overlay.py",
            "aworld/self_evolve/controllers/run_replay_adaptation.py",
        ),
        (
            ("aworld/self_evolve/replay.py", "build_replay_request"),
            (
                "aworld/self_evolve/controllers/run_replay_adaptation.py",
                "prepare_replay_adaptation",
            ),
        ),
    ),
    _ComponentSpec(
        "evaluation",
        "Evaluate selection evidence, independent regression, and counterexamples.",
        (
            "aworld/self_evolve/evaluation.py",
            "aworld/self_evolve/judge.py",
            "aworld/self_evolve/runtime_health.py",
            "aworld/self_evolve/regression.py",
            "aworld/self_evolve/challenger.py",
        ),
        (
            (
                "aworld/self_evolve/evaluation.py",
                "evaluate_baseline_and_candidate",
            ),
            ("aworld/self_evolve/regression.py", "RegressionEvidence"),
            ("aworld/self_evolve/challenger.py", "ChallengerBackend"),
        ),
    ),
    _ComponentSpec(
        "learning",
        "Extract typed lessons, causal diagnostics, and reusable feedback.",
        (
            "aworld/self_evolve/lessons.py",
            "aworld/self_evolve/diagnostics.py",
            "aworld/self_evolve/feedback.py",
            "aworld/self_evolve/evidence_diagnostics.py",
        ),
        (("aworld/self_evolve/lessons.py", "extract_lesson_records"),),
    ),
    _ComponentSpec(
        "apply",
        "Journal, verify, publish or isolate candidate artifacts, and recover safely.",
        (
            "aworld/self_evolve/store.py",
            "aworld/self_evolve/targets.py",
            "aworld/self_evolve/release_checks.py",
            "aworld/self_evolve/lifecycle.py",
        ),
        (
            (
                "aworld/self_evolve/store.py",
                "FilesystemSelfEvolveStore.write_apply_backup",
            ),
            (
                "aworld/self_evolve/targets.py",
                "SkillTextTarget.apply_candidate_variant",
            ),
        ),
    ),
    _ComponentSpec(
        "orchestration",
        "Own the run/campaign state machine, budgets, scheduling, and terminal reports.",
        (
            "aworld/self_evolve/runner.py",
            "aworld/self_evolve/controllers/run_lifecycle_execution.py",
            "aworld/self_evolve/controllers/run_lifecycle_bootstrap_execution.py",
            "aworld/self_evolve/controllers/run_lifecycle_iteration_execution.py",
            "aworld/self_evolve/controllers/run_lifecycle_terminal_execution.py",
            "aworld/self_evolve/controllers/run_phase_assembly.py",
            "aworld/self_evolve/controllers/run_screening_phases.py",
            "aworld/self_evolve/controllers/run_measurement_phases.py",
            "aworld/self_evolve/controllers/run_candidate_phases.py",
            "aworld/self_evolve/controllers/run_apply_phases.py",
            "aworld/self_evolve/campaign.py",
            "aworld/self_evolve/scheduler.py",
            "aworld/self_evolve/runtime.py",
            "aworld/self_evolve/budget.py",
        ),
        (
            ("aworld/self_evolve/runner.py", "SelfEvolveRunner.run_explicit_target"),
            (
                "aworld/self_evolve/controllers/run_lifecycle_execution.py",
                "RunLifecycleExecution.execute",
            ),
            (
                "aworld/self_evolve/controllers/run_phase_assembly.py",
                "assemble_run_phases",
            ),
            (
                "aworld/self_evolve/controllers/run_candidate_phases.py",
                "CandidatePhaseFactory._candidate_iteration_execution",
            ),
            (
                "aworld/self_evolve/campaign.py",
                "SelfImprovementCampaignController.advance_once",
            ),
            ("aworld/self_evolve/budget.py", "StageAwareCandidateScheduler.schedule"),
        ),
    ),
)


_STATE_SPECS = (
    _StateSpec(
        "target_fingerprint",
        "Identity boundary between evaluated and applied target content.",
        (("aworld/self_evolve/optimizers/base.py", "OptimizerRequest.from_dataset"),),
        (
            (
                "aworld/self_evolve/targets.py",
                "SkillTextTarget.apply_candidate_variant",
            ),
        ),
        (
            (
                "aworld/self_evolve/controllers/run_apply_transaction.py",
                "execute_apply_transaction",
            ),
        ),
        (),
        "Durable in candidate, campaign, provenance, and apply journal artifacts.",
    ),
    _StateSpec(
        "environment_fingerprint",
        "Replay dependency and workspace identity held stable across candidates.",
        (
            (
                "aworld/self_evolve/controllers/run_replay_adaptation.py",
                "ReplayAdaptationState",
            ),
        ),
        (
            (
                "aworld/self_evolve/controllers/run_replay_adaptation.py",
                "prepare_replay_adaptation",
            ),
        ),
        (
            (
                "aworld/self_evolve/replay_gates.py",
                "_environment_fingerprint_drift_gate",
            ),
        ),
        (
            (
                "aworld/self_evolve/controllers/run_replay_adaptation.py",
                "ReplayAdaptationState.cleanup_run",
            ),
        ),
        "Run-scoped; durable fingerprint remains in replay evidence.",
    ),
    _StateSpec(
        "candidate_lineage",
        "Candidate ancestry, source disposition, lessons, and lifecycle outcome.",
        (("aworld/self_evolve/types.py", "OptimizerLineage"),),
        (("aworld/self_evolve/lineage_history.py", "_persist_lineage_lifecycle"),),
        (
            (
                "aworld/self_evolve/store.py",
                "FilesystemSelfEvolveStore.write_optimizer_lineage",
            ),
        ),
        (("aworld/self_evolve/lifecycle.py", "_candidate_materialization_paths"),),
        "Selected lineage is durable; non-selected materializations follow retention policy.",
    ),
    _StateSpec(
        "budget_ledger",
        "Reservation, debit, release, and cumulative usage authority.",
        (("aworld/self_evolve/budget.py", "RunBudgetLedger"),),
        (("aworld/self_evolve/budget.py", "RunBudgetLedger.remaining"),),
        (
            (
                "aworld/self_evolve/controllers/run_resources.py",
                "RunBudgetContext.debit",
            ),
        ),
        (
            (
                "aworld/self_evolve/controllers/run_resources.py",
                "RunBudgetContext.release_all",
            ),
        ),
        "Run report and campaign usage persist after outstanding reservations close.",
    ),
    _StateSpec(
        "apply_journal",
        "Crash-safe record of backup, application, verification, rollback, and publish.",
        (
            (
                "aworld/self_evolve/store.py",
                "FilesystemSelfEvolveStore.write_apply_backup",
            ),
        ),
        (
            (
                "aworld/self_evolve/store.py",
                "FilesystemSelfEvolveStore.recover_interrupted_apply",
            ),
        ),
        (
            (
                "aworld/self_evolve/store.py",
                "FilesystemSelfEvolveStore.update_apply_journal",
            ),
        ),
        (("aworld/self_evolve/lifecycle.py", "_has_interrupted_apply"),),
        "Durable while referenced by terminal report; interrupted journals block cleanup.",
    ),
    _StateSpec(
        "campaign_frontier",
        "Typed failure frontier used to decide repair, shared blocker, handoff, or exhaustion.",
        (
            (
                "aworld/self_evolve/controllers/run_generation_helpers.py",
                "_typed_repair_frontiers",
            ),
        ),
        (("aworld/self_evolve/budget.py", "StageAwareCandidateScheduler.schedule"),),
        (
            (
                "aworld/self_evolve/campaign.py",
                "SelfImprovementCampaignController.advance_once",
            ),
        ),
        (("aworld/self_evolve/campaign.py", "_limit_campaign"),),
        "Latest frontier and semantic identities persist in campaign checkpoint.",
    ),
)


_STAGE_SPECS = (
    _StageSpec(
        "prepare",
        "orchestration",
        ("ingestion",),
        ("aworld/self_evolve/runner.py", "optimize_from_cli_request"),
    ),
    _StageSpec(
        "ingestion",
        "ingestion",
        ("target_selection", "complete"),
        ("aworld/self_evolve/datasets.py", "build_dataset_from_source"),
    ),
    _StageSpec(
        "target_selection",
        "target_selection",
        ("candidate_generation", "complete"),
        (
            "aworld/self_evolve/credit_assignment.py",
            "build_target_selection_decision",
        ),
    ),
    _StageSpec(
        "candidate_generation",
        "candidate_generation",
        ("conformance", "complete"),
        ("aworld/self_evolve/candidate_generation.py", "CandidateGenerationAgent"),
    ),
    _StageSpec(
        "conformance",
        "conformance",
        ("replay", "evaluation", "candidate_generation"),
        ("aworld/self_evolve/gates.py", "CandidatePackageGate"),
    ),
    _StageSpec(
        "replay",
        "replay",
        ("evaluation", "candidate_generation"),
        ("aworld/self_evolve/replay.py", "build_replay_request"),
    ),
    _StageSpec(
        "evaluation",
        "evaluation",
        ("independent_regression", "candidate_generation"),
        ("aworld/self_evolve/evaluation.py", "evaluate_baseline_and_candidate"),
    ),
    _StageSpec(
        "independent_regression",
        "evaluation",
        ("apply", "candidate_generation"),
        (
            "aworld/self_evolve/controllers/run_regression_execution.py",
            "execute_independent_regression",
        ),
    ),
    _StageSpec(
        "apply",
        "apply",
        ("complete",),
        (
            "aworld/self_evolve/controllers/run_apply_transaction.py",
            "execute_apply_transaction",
        ),
    ),
    _StageSpec(
        "complete",
        "orchestration",
        (),
        ("aworld/self_evolve/store.py", "FilesystemSelfEvolveStore.write_report"),
        terminal=True,
    ),
)


def refresh_handbook_snapshot(
    workspace_root: str | Path,
    *,
    previous: HandbookSnapshot | None = None,
    changed_paths: Iterable[str | Path] = (),
) -> HandbookSnapshot:
    """Refresh changed modules and reuse only byte-identical prior AST indexes."""

    workspace = Path(workspace_root).expanduser().resolve(strict=True)
    source_root = (workspace / HANDBOOK_SOURCE_ROOT).resolve(strict=True)
    if not source_root.is_dir() or not source_root.is_relative_to(workspace):
        raise ValueError("self-evolve handbook source root is unavailable")

    previous_modules = (
        {item.relative_path: item for item in previous.modules}
        if previous is not None
        else {}
    )
    explicitly_changed = {
        _workspace_relative_path(workspace, value) for value in changed_paths
    }
    current_paths = {
        path.relative_to(workspace).as_posix(): path
        for path in source_root.rglob("*.py")
        if path.is_file() and not path.is_symlink()
    }
    modules: list[HandbookModule] = []
    for relative_path, path in sorted(current_paths.items()):
        content = path.read_bytes()
        fingerprint = _bytes_fingerprint(content)
        prior = previous_modules.get(relative_path)
        if (
            prior is not None
            and relative_path not in explicitly_changed
            and prior.fingerprint == fingerprint
            and prior.status == LOCATOR_ACTIVE
        ):
            modules.append(prior)
            continue
        modules.append(_index_module(relative_path, content, fingerprint))

    for relative_path, prior in sorted(previous_modules.items()):
        if relative_path in current_paths:
            continue
        modules.append(
            HandbookModule(
                relative_path=relative_path,
                fingerprint=prior.fingerprint,
                locators=tuple(
                    replace(
                        locator,
                        status=LOCATOR_FROZEN,
                        error="source module was deleted",
                    )
                    for locator in prior.locators
                ),
                status=LOCATOR_FROZEN,
                error="source module was deleted",
            )
        )

    module_tuple = tuple(sorted(modules, key=lambda item: item.relative_path))
    locator_index = _locator_index(module_tuple)
    return HandbookSnapshot(
        modules=module_tuple,
        components=_resolve_components(locator_index),
        state_register=_resolve_state_register(locator_index),
        stages=_resolve_stages(locator_index),
    )


def load_or_refresh_handbook(
    workspace_root: str | Path,
    *,
    snapshot_path: str | Path | None = None,
    changed_paths: Iterable[str | Path] = (),
) -> HandbookSnapshot:
    workspace = Path(workspace_root).expanduser().resolve(strict=True)
    requested_path = (
        Path(snapshot_path).expanduser()
        if snapshot_path is not None
        else Path(".aworld/self_evolve/handbook/snapshot.json")
    )
    path = (
        requested_path if requested_path.is_absolute() else workspace / requested_path
    ).resolve(strict=False)
    if not path.is_relative_to(workspace) or path.is_symlink():
        raise ValueError("handbook snapshot path must remain inside the workspace")
    previous: HandbookSnapshot | None = None
    if path.is_file() and not path.is_symlink():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, Mapping):
                previous = HandbookSnapshot.from_dict(value)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            previous = None
    snapshot = refresh_handbook_snapshot(
        workspace,
        previous=previous,
        changed_paths=changed_paths,
    )
    _write_snapshot(path, snapshot)
    return snapshot


def load_handbook_slice_for_target(
    workspace_root: str | Path,
    *,
    target_path: str | Path | None,
    behavior_signals: Sequence[str] = (),
    snapshot_path: str | Path | None = None,
) -> HandbookSlice | None:
    if target_path is None:
        return None
    workspace = Path(workspace_root).expanduser().resolve(strict=True)
    relative_target = _workspace_relative_path(workspace, target_path)
    if not (
        relative_target == HANDBOOK_SOURCE_ROOT.as_posix()
        or relative_target.startswith(HANDBOOK_SOURCE_ROOT.as_posix() + "/")
    ):
        return None
    snapshot = load_or_refresh_handbook(
        workspace,
        snapshot_path=snapshot_path,
    )
    return handbook_slice_for_target(
        snapshot,
        workspace_root=workspace,
        target_path=relative_target,
        behavior_signals=behavior_signals,
    )


def handbook_slice_for_target(
    snapshot: HandbookSnapshot,
    *,
    workspace_root: str | Path,
    target_path: str | Path | None,
    behavior_signals: Sequence[str] = (),
) -> HandbookSlice | None:
    if target_path is None:
        return None
    workspace = Path(workspace_root).expanduser().resolve(strict=True)
    relative_target = _workspace_relative_path(workspace, target_path)
    if not (
        relative_target == HANDBOOK_SOURCE_ROOT.as_posix()
        or relative_target.startswith(HANDBOOK_SOURCE_ROOT.as_posix() + "/")
    ):
        return None

    normalized_signals = " ".join(behavior_signals).casefold()
    selected_components = tuple(
        component
        for component in snapshot.components
        if any(
            relative_target == module_path
            or relative_target.startswith(module_path.rstrip("/") + "/")
            for module_path in component.module_paths
        )
        or (
            normalized_signals
            and any(
                token in normalized_signals
                for token in component.component_id.split("_")
            )
        )
    )
    component_ids = tuple(item.component_id for item in selected_components)
    selected_states = tuple(
        entry
        for entry in snapshot.state_register
        if any(locator.relative_path == relative_target for locator in entry.locators)
        or any(token in normalized_signals for token in entry.state_id.split("_"))
    )
    selected_stages = tuple(
        stage for stage in snapshot.stages if stage.component_id in component_ids
    )
    locators = _unique_locators(
        (
            *(
                locator
                for component in selected_components
                for locator in component.entry_locators
            ),
            *(locator for entry in selected_states for locator in entry.locators),
            *(stage.entry_locator for stage in selected_stages),
        )
    )[:MAX_HANDBOOK_SLICE_LOCATORS]
    frozen = tuple(
        locator.locator_id
        for locator in locators
        if locator.status != LOCATOR_ACTIVE
        or not validate_source_locator(locator, workspace_root=workspace)
    )
    return HandbookSlice(
        target_path=relative_target,
        component_ids=component_ids,
        components=selected_components,
        state_register=selected_states,
        stages=selected_stages,
        frozen_locator_ids=tuple(dict.fromkeys(frozen)),
        snapshot_fingerprint=snapshot.fingerprint,
    )


def validate_source_locator(
    locator: SourceLocator,
    *,
    workspace_root: str | Path,
) -> bool:
    if locator.status != LOCATOR_ACTIVE:
        return False
    workspace = Path(workspace_root).expanduser().resolve(strict=True)
    path = (workspace / locator.relative_path).resolve(strict=False)
    if not path.is_relative_to(workspace) or not path.is_file() or path.is_symlink():
        return False
    content = path.read_bytes()
    if _bytes_fingerprint(content) != locator.module_fingerprint:
        return False
    indexed = _index_module(locator.relative_path, content, locator.module_fingerprint)
    current = {item.qualified_symbol: item for item in indexed.locators}.get(
        locator.qualified_symbol
    )
    return current is not None and current == locator


def _index_module(
    relative_path: str,
    content: bytes,
    fingerprint: str,
) -> HandbookModule:
    try:
        source = content.decode("utf-8")
        tree = ast.parse(source, filename=relative_path)
    except (UnicodeDecodeError, SyntaxError) as exc:
        return HandbookModule(
            relative_path=relative_path,
            fingerprint=fingerprint,
            locators=(),
            status=LOCATOR_FROZEN,
            error=f"{type(exc).__name__}: {exc}",
        )
    lines = source.splitlines(keepends=True)
    locators: list[SourceLocator] = []

    def visit(nodes: Iterable[ast.stmt], prefix: str = "") -> None:
        for node in nodes:
            if not isinstance(
                node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            qualified = f"{prefix}.{node.name}" if prefix else node.name
            start = node.lineno
            end = node.end_lineno or node.lineno
            source_bytes = "".join(lines[start - 1 : end]).encode("utf-8")
            locators.append(
                SourceLocator(
                    relative_path=relative_path,
                    qualified_symbol=qualified,
                    symbol_kind=(
                        "class"
                        if isinstance(node, ast.ClassDef)
                        else "async_function"
                        if isinstance(node, ast.AsyncFunctionDef)
                        else "function"
                    ),
                    start_line=start,
                    end_line=end,
                    module_fingerprint=fingerprint,
                    source_fingerprint=_bytes_fingerprint(source_bytes),
                )
            )
            if isinstance(node, ast.ClassDef):
                visit(node.body, qualified)

    visit(tree.body)
    return HandbookModule(
        relative_path=relative_path,
        fingerprint=fingerprint,
        locators=tuple(locators),
    )


def _resolve_components(
    locator_index: Mapping[tuple[str, str], SourceLocator],
) -> tuple[HandbookComponent, ...]:
    components: list[HandbookComponent] = []
    for spec in _COMPONENT_SPECS:
        locators = tuple(
            _resolve_locator(locator_index, path, symbol)
            for path, symbol in spec.entries
        )
        components.append(
            HandbookComponent(
                component_id=spec.component_id,
                responsibility=spec.responsibility,
                module_paths=spec.module_paths,
                entry_locators=locators,
                status=(
                    LOCATOR_ACTIVE
                    if all(item.status == LOCATOR_ACTIVE for item in locators)
                    else LOCATOR_FROZEN
                ),
            )
        )
    return tuple(components)


def _resolve_state_register(
    locator_index: Mapping[tuple[str, str], SourceLocator],
) -> tuple[StateRegisterEntry, ...]:
    entries: list[StateRegisterEntry] = []
    for spec in _STATE_SPECS:
        resolve = lambda values: tuple(  # noqa: E731
            _resolve_locator(locator_index, path, symbol) for path, symbol in values
        )
        create = resolve(spec.create)
        read = resolve(spec.read)
        update = resolve(spec.update)
        cleanup = resolve(spec.cleanup)
        all_locators = (*create, *read, *update, *cleanup)
        entries.append(
            StateRegisterEntry(
                state_id=spec.state_id,
                description=spec.description,
                create=create,
                read=read,
                update=update,
                cleanup=cleanup,
                retention=spec.retention,
                status=(
                    LOCATOR_ACTIVE
                    if create
                    and read
                    and all(item.status == LOCATOR_ACTIVE for item in all_locators)
                    else LOCATOR_FROZEN
                ),
            )
        )
    return tuple(entries)


def _resolve_stages(
    locator_index: Mapping[tuple[str, str], SourceLocator],
) -> tuple[HandbookStage, ...]:
    return tuple(
        HandbookStage(
            stage_id=spec.stage_id,
            component_id=spec.component_id,
            next_stages=spec.next_stages,
            failure_exits=(() if spec.terminal else (f"{spec.stage_id}_failed",)),
            entry_locator=_resolve_locator(locator_index, *spec.entry),
            terminal=spec.terminal,
        )
        for spec in _STAGE_SPECS
    )


def _resolve_locator(
    locator_index: Mapping[tuple[str, str], SourceLocator],
    relative_path: str,
    qualified_symbol: str,
) -> SourceLocator:
    locator = locator_index.get((relative_path, qualified_symbol))
    if locator is not None:
        return locator
    return SourceLocator(
        relative_path=relative_path,
        qualified_symbol=qualified_symbol,
        symbol_kind="unknown",
        start_line=None,
        end_line=None,
        module_fingerprint="missing",
        source_fingerprint=None,
        status=LOCATOR_FROZEN,
        error="source symbol could not be resolved",
    )


def _locator_index(
    modules: Iterable[HandbookModule],
) -> dict[tuple[str, str], SourceLocator]:
    return {
        (locator.relative_path, locator.qualified_symbol): locator
        for module in modules
        for locator in module.locators
    }


def _unique_locators(values: Iterable[SourceLocator]) -> tuple[SourceLocator, ...]:
    unique: dict[str, SourceLocator] = {}
    for locator in values:
        unique.setdefault(locator.locator_id, locator)
    return tuple(unique.values())


def _workspace_relative_path(workspace: Path, value: str | Path) -> str:
    path = Path(value).expanduser()
    absolute = path if path.is_absolute() else workspace / path
    resolved = absolute.resolve(strict=False)
    if not resolved.is_relative_to(workspace):
        raise ValueError("handbook path must remain inside the workspace")
    return resolved.relative_to(workspace).as_posix()


def _write_snapshot(path: Path, snapshot: HandbookSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _bytes_fingerprint(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _bytes_fingerprint(encoded)


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str))
