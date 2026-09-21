from __future__ import annotations

import ast
import inspect
from pathlib import Path

from aworld.self_evolve.controllers import (
    run_terminal_finalization as finalization_module,
)
from aworld.self_evolve.controllers.run_terminal_finalization import (
    TerminalFinalizationRequest,
    TerminalFinalizationRuntime,
    finalize_terminal_run,
)
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    EvaluationSummary,
    GateResult,
    SelfEvolveRunStatus,
    SelfEvolveTargetRef,
)


def _target() -> SelfEvolveTargetRef:
    return SelfEvolveTargetRef("skill", "demo", "/tmp/demo/SKILL.md")


def _candidate() -> CandidateVariant:
    return CandidateVariant(
        candidate_id="candidate-1",
        target=_target(),
        content="# Improved\n",
        rationale="exercise terminal finalization",
    )


def _dataset() -> SelfEvolveDataset:
    return SelfEvolveDataset(
        cases=(EvalCase(case_id="case-1", input={"task": "demo"}),),
        recipe=DatasetRecipe(
            source={"kind": "controller-test"},
            split_seed="seed",
            splits={"train": ["case-1"]},
            trainable_case_ids=("case-1",),
        ),
    )


class _Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.lessons = ()
        self.diagnostics = ()

    def read_all_candidate_attempt_events(self, _run_id: str):
        return []

    def write_lesson_records(self, _run_id: str, lessons):
        self.lessons = lessons
        return self.root / "lessons.jsonl"

    def write_harness_diagnostics(self, _run_id: str, diagnostics):
        self.diagnostics = diagnostics
        return self.root / "harness.jsonl"


def _request() -> TerminalFinalizationRequest:
    failed_gate = GateResult(
        gate_name="evaluation",
        passed=False,
        reason="evaluation backend failed",
        details={
            "failure_class": "candidate",
            "code": "evaluation_failed",
        },
    )
    feedback = EvaluationSummary(
        variant_id="candidate-1",
        dataset_split="validation",
        metrics={
            "failed_gates": ["evaluation"],
            "candidate_status": "rejected",
        },
    )
    return TerminalFinalizationRequest(
        run_id="run-1",
        target=_target(),
        final_status=SelfEvolveRunStatus.REJECTED,
        reported_selected_candidate=_candidate(),
        repair_focus_candidate=None,
        apply_policy="proposal",
        base_report={"run_id": "run-1", "status": "rejected"},
        optimizer_diagnostics=(),
        gate_results=(failed_gate,),
        scheduler_decisions=({"reason_code": "candidate_rejected"},),
        population_screening_reports=(),
        iteration_states=({"feedback": (feedback,)},),
        iteration_reports=(
            {"candidate_id": "candidate-1", "status": "rejected"},
        ),
        generation_stop_reason=None,
        dataset=_dataset(),
        all_candidates=(_candidate(),),
        replay_candidate_limit=1,
        budget_report={"ledger": {}},
        optimizer_lineage_paths=("lineage/candidate-1.json",),
        target_selection_report=None,
        post_apply=None,
        promotion=None,
        baseline_summary=None,
        candidate_summary=feedback,
        held_out_summary=None,
        replay_result=None,
        replay_dataset=None,
        skill_evolution_progress=None,
        trace_packs=(),
        candidate_source_dispositions={},
        deprecated_config_mappings=("legacy-budget",),
        previous_artifact_retention={"status": "started"},
    )


def _runtime(store: _Store, persisted: dict) -> TerminalFinalizationRuntime:
    def persist(_store, run_id, **kwargs):
        persisted.update({"run_id": run_id, **kwargs})
        return store.root / "report.json"

    return TerminalFinalizationRuntime(
        store=store,
        terminal_cause=lambda **_kwargs: None,
        rejection_attribution=lambda **_kwargs: {
            "code": "evaluation_failed"
        },
        resolved_contract_fingerprints=lambda _reports: ("contract-a",),
        campaign_failure_attribution=lambda *_args, **_kwargs: {
            "primary_gate": "evaluation"
        },
        trajectory_set_report=lambda _dataset: None,
        population_report=lambda **_kwargs: {"generated_candidate_count": 1},
        no_op_report=lambda *_args: None,
        replay_report=lambda _replay: {},
        replay_artifact_path=lambda _replay: None,
        campaign_measurement_outcome=lambda *_args, **_kwargs: None,
        replay_capability_report=lambda _replay: None,
        evaluator_report_paths=lambda *_summaries: (),
        acceptance_confidence_report=lambda _gates: None,
        finalize_run_report=persist,
    )


def test_terminal_finalization_enriches_and_persists_one_transaction(
    tmp_path: Path,
) -> None:
    store = _Store(tmp_path)
    persisted: dict = {}

    result = finalize_terminal_run(
        _request(),
        runtime=_runtime(store, persisted),
    )

    assert result.completed_run.status is SelfEvolveRunStatus.REJECTED
    assert result.completed_run.selected_candidate_id == "candidate-1"
    assert result.report_path == tmp_path / "report.json"
    assert result.report["rejection_attribution"] == {
        "code": "evaluation_failed"
    }
    assert result.report["resolved_conformance_frontiers"] == {
        "count": 1,
        "contract_fingerprints": ["contract-a"],
    }
    assert result.report["population"] == {"generated_candidate_count": 1}
    assert result.report["deprecated_config_mappings"] == ["legacy-budget"]
    assert result.report["optimizer_lineage"] == {
        "count": 1,
        "paths": ["lineage/candidate-1.json"],
    }
    assert result.report["lessons"]["count"] == len(store.lessons)
    assert result.report["harness_diagnostics"]["count"] == len(
        store.diagnostics
    )
    assert persisted["completed_run"] == result.completed_run
    assert persisted["previous_artifact_retention"] == {"status": "started"}


def test_terminal_finalization_controller_does_not_import_runner() -> None:
    tree = ast.parse(inspect.getsource(finalization_module))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    assert "aworld.self_evolve.runner" not in imported_modules
