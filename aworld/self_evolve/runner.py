from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Callable, Any
from pathlib import Path
from typing import Mapping, Iterable

from aworld.self_evolve.credit_assignment import (
    TargetInventoryEntry,
    TargetSelectionReport,
    TrajectoryCreditAssigner,
    build_default_target_inventory,
)
from aworld.self_evolve.datasets import (
    SelfEvolveDataset,
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.evaluation import (
    EvaluationBackend,
    EvaluationRequest,
    determine_candidate_confidence,
    estimate_replay_cost,
    evaluate_baseline_and_candidate,
)
from aworld.self_evolve.gates import (
    BudgetGate,
    CostLatencyRegressionGate,
    ExternalCodeEvolutionGate,
    GlobalRegressionBenchmarkGate,
    HeldOutVerificationGate,
    JudgeOnlySignalGate,
    MalformedCandidateGate,
    NoopCandidateGate,
    ProtectedPathGate,
    RequiredVerificationGate,
    ScoreImprovementGate,
    SkillMarkdownGate,
    StoppingConditionGate,
    StoppingConditionState,
    TokenLimitGate,
    TrustProvenanceGate,
)
from aworld.self_evolve.optimizers.base import CandidateOptimizer, OptimizerRequest
from aworld.self_evolve.optimizers.llm_mutator import TraceReflectiveLLMMutator
from aworld.self_evolve.provenance import TargetProvenance
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SelfEvolveTarget, SkillTextTarget
from aworld.self_evolve.trace_pack import TracePack
from aworld.self_evolve.types import (
    CandidateVariant,
    EvaluationSummary,
    GateResult,
    SelfEvolveRun,
    SelfEvolveRunStatus,
    SelfEvolveTargetRef,
    to_json_dict,
)


@dataclass(frozen=True)
class SelfEvolveRunnerResult:
    run: SelfEvolveRun
    selected_candidate: CandidateVariant | None


class SelfEvolveRunner:
    def __init__(
        self,
        *,
        store: FilesystemSelfEvolveStore,
        optimizer: CandidateOptimizer,
        post_apply_evaluator: Callable[[CandidateVariant], Any] | None = None,
        evaluation_backend: EvaluationBackend | None = None,
        min_score_delta: float = 0.0,
        pending_duplicate: bool = False,
        max_iterations: int = 1,
        min_eval_cases: int = 30,
        judge_repetitions: int = 3,
        max_run_tokens: int = 500_000,
        auto_apply_target_types: tuple[str, ...] = ("skill",),
    ) -> None:
        self.store = store
        self.optimizer = optimizer
        self.post_apply_evaluator = post_apply_evaluator
        self.evaluation_backend = evaluation_backend
        self.min_score_delta = min_score_delta
        self.pending_duplicate = pending_duplicate
        self.max_iterations = max_iterations
        self.min_eval_cases = min_eval_cases
        self.judge_repetitions = judge_repetitions
        self.max_run_tokens = max_run_tokens
        self.auto_apply_target_types = tuple(auto_apply_target_types)

    async def run_explicit_target(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        trace_packs: tuple[TracePack, ...],
        apply_policy: str = "proposal",
        target_selection_report: TargetSelectionReport | None = None,
        target_provenance: TargetProvenance | None = None,
    ) -> SelfEvolveRunnerResult:
        if apply_policy not in {"proposal", "auto_verified"}:
            raise ValueError(f"unsupported apply policy: {apply_policy}")

        run = SelfEvolveRun(run_id=run_id, target=target.identity, status=SelfEvolveRunStatus.RUNNING)
        self.store.create_run(run)
        self.store.write_dataset_recipe(run_id, dataset.recipe)
        if target_selection_report is not None:
            self.store.write_target_selection_report(run_id, target_selection_report)
        if target_provenance is not None:
            self.store.write_target_provenance(run_id, target_provenance)

        stopping_gate = StoppingConditionGate(
            max_iterations=self.max_iterations,
            max_stalled_iterations=1,
            max_repeated_gate_failures=1,
        )
        stopping_result = stopping_gate.evaluate(
            StoppingConditionState(iteration=0, pending_duplicate=self.pending_duplicate)
        )
        if not stopping_result.passed:
            report = {
                "run_id": run_id,
                "target": {
                    "target_type": target.identity.target_type,
                    "target_id": target.identity.target_id,
                    "path": target.identity.path,
                },
                "apply_policy": apply_policy,
                "candidate_ids": [],
                "selected_candidate_id": None,
                "status": SelfEvolveRunStatus.REJECTED.value,
                "stopping_condition": {
                    "gate_name": stopping_result.gate_name,
                    "passed": stopping_result.passed,
                    "reason": stopping_result.reason,
                    "details": stopping_result.details,
                },
            }
            if target_selection_report is not None:
                report["target_selection"] = to_json_dict(target_selection_report)
            self.store.write_report(run_id, report)
            completed_run = SelfEvolveRun(
                run_id=run_id,
                target=target.identity,
                status=SelfEvolveRunStatus.REJECTED,
                gate_results=(stopping_result,),
            )
            self.store.create_run(completed_run)
            return SelfEvolveRunnerResult(run=completed_run, selected_candidate=None)

        optimizer_result = await self.optimizer.propose(
            OptimizerRequest.from_dataset(
                target=target.identity,
                current_content=target.load_current_content(),
                target_fingerprint=target.fingerprint_current_content(),
                trace_packs=trace_packs,
                validation_feedback=(),
                dataset=dataset,
            )
        )

        selected_candidate = optimizer_result.candidates[0] if optimizer_result.candidates else None
        for candidate in optimizer_result.candidates:
            target.preserve_proposal(self.store, run_id, candidate)
        for lineage in optimizer_result.lineage:
            self.store.write_optimizer_lineage(run_id, lineage)

        baseline_summary: EvaluationSummary | None = None
        candidate_summary: EvaluationSummary | None = None
        held_out_summary: EvaluationSummary | None = None
        gate_results = []
        if selected_candidate is not None:
            current_content = target.load_current_content()
            gate_results.extend(
                _candidate_gate_results(
                    selected_candidate,
                    current_content=current_content,
                    workspace_root=self.store.workspace_root,
                    max_chars=self.max_run_tokens,
                    target_provenance=target_provenance,
                )
            )
            gate_results.append(
                BudgetGate().evaluate(
                    estimate_replay_cost(
                        dataset=dataset,
                        candidate_count=len(optimizer_result.candidates),
                        judge_repetitions=self.judge_repetitions,
                        max_run_tokens=self.max_run_tokens,
                    )
                )
            )
        if self.evaluation_backend is not None and selected_candidate is not None:
            baseline_summary, candidate_summary = await evaluate_baseline_and_candidate(
                self.evaluation_backend,
                dataset=dataset,
                candidate=selected_candidate,
                dataset_split="validation",
            )
            score_gate = ScoreImprovementGate(min_delta=self.min_score_delta).evaluate(
                baseline=baseline_summary,
                candidate=candidate_summary,
            )
            cost_latency_gate = CostLatencyRegressionGate(
                max_cost_regression_ratio=0.25,
                max_latency_regression_ratio=0.5,
            ).evaluate(baseline=baseline_summary, candidate=candidate_summary)
            gate_results.extend([score_gate, cost_latency_gate])
            if apply_policy == "auto_verified":
                held_out_summary = await self.evaluation_backend.evaluate_variant(
                    EvaluationRequest(
                        variant_id=selected_candidate.candidate_id,
                        candidate=selected_candidate,
                        dataset=dataset,
                        dataset_split="held_out",
                    )
                )
                confidence = determine_candidate_confidence(
                    dataset=dataset,
                    validation_summary=candidate_summary,
                    held_out_summary=held_out_summary,
                    min_eval_cases=self.min_eval_cases,
                )
                gate_results.extend(
                    [
                        RequiredVerificationGate().evaluate(held_out_summary),
                        HeldOutVerificationGate(min_eval_cases=self.min_eval_cases).evaluate(confidence),
                        JudgeOnlySignalGate().evaluate(confidence),
                        GlobalRegressionBenchmarkGate().evaluate(
                            selected_candidate,
                            held_out_summary,
                        ),
                    ]
                )
        elif apply_policy == "auto_verified" and selected_candidate is not None:
            gate_results.append(
                GateResult(
                    gate_name="auto_verified_evaluation",
                    passed=False,
                    reason="auto_verified apply policy requires evaluation backend",
                )
            )

        if apply_policy == "auto_verified" and selected_candidate is not None:
            gate_results.append(
                GateResult(
                    gate_name="auto_apply_target_type",
                    passed=target.identity.target_type in self.auto_apply_target_types,
                    reason=(
                        "target type is allowlisted for auto apply"
                        if target.identity.target_type in self.auto_apply_target_types
                        else "target type is not allowlisted for auto apply"
                    ),
                    details={
                        "target_type": target.identity.target_type,
                        "auto_apply_target_types": list(self.auto_apply_target_types),
                    },
                )
            )

        post_apply: dict[str, object] | None = None
        final_status = SelfEvolveRunStatus.SUCCEEDED
        if apply_policy == "auto_verified" and selected_candidate is not None:
            failed_gates = [gate for gate in gate_results if not gate.passed]
            if failed_gates:
                final_status = SelfEvolveRunStatus.REJECTED
            else:
                post_apply = await self._apply_auto_verified(target, selected_candidate)
                if post_apply["status"] != "accepted":
                    final_status = SelfEvolveRunStatus.REJECTED

        report = {
            "run_id": run_id,
            "target": {
                "target_type": target.identity.target_type,
                "target_id": target.identity.target_id,
                "path": target.identity.path,
            },
            "apply_policy": apply_policy,
            "candidate_ids": [
                candidate.candidate_id for candidate in optimizer_result.candidates
            ],
            "selected_candidate_id": (
                selected_candidate.candidate_id if selected_candidate is not None else None
            ),
            "status": final_status.value,
            "optimizer_diagnostics": optimizer_result.diagnostics,
        }
        if target_selection_report is not None:
            report["target_selection"] = to_json_dict(target_selection_report)
        if post_apply is not None:
            report["post_apply"] = post_apply
        if baseline_summary is not None:
            report["baseline_metrics"] = dict(baseline_summary.metrics)
        if candidate_summary is not None:
            report["candidate_metrics"] = dict(candidate_summary.metrics)
        if held_out_summary is not None:
            report["held_out_metrics"] = dict(held_out_summary.metrics)
        if gate_results:
            report["gate_results"] = [
                {
                    "gate_name": gate_result.gate_name,
                    "passed": gate_result.passed,
                    "reason": gate_result.reason,
                    "details": gate_result.details,
                }
                for gate_result in gate_results
            ]
        self.store.write_report(run_id, report)

        completed_run = SelfEvolveRun(
            run_id=run_id,
            target=target.identity,
            status=final_status,
            selected_candidate_id=(
                selected_candidate.candidate_id if selected_candidate is not None else None
            ),
            metrics=tuple(item for item in (baseline_summary, candidate_summary) if item is not None),
            gate_results=tuple(gate_results),
        )
        self.store.create_run(completed_run)
        return SelfEvolveRunnerResult(run=completed_run, selected_candidate=selected_candidate)

    async def _apply_auto_verified(
        self,
        target: SelfEvolveTarget,
        candidate: CandidateVariant,
    ) -> dict[str, object]:
        if self.post_apply_evaluator is None:
            raise ValueError("auto_verified apply policy requires post_apply_evaluator")
        target.apply_candidate(candidate.content)
        summary = self.post_apply_evaluator(candidate)
        if inspect.isawaitable(summary):
            summary = await summary
        if not isinstance(summary, EvaluationSummary):
            raise ValueError("post_apply_evaluator must return EvaluationSummary")
        if summary.metrics.get("post_apply_passed") is True:
            return {
                "status": "accepted",
                "metrics": dict(summary.metrics),
                "dataset_split": summary.dataset_split,
            }

        target.rollback()
        return {
            "status": "rolled_back",
            "metrics": dict(summary.metrics),
            "dataset_split": summary.dataset_split,
        }


async def optimize_explicit_target(
    *,
    workspace_root: str | Path,
    run_id: str,
    target: SelfEvolveTarget,
    current_trajectory: Iterable[Mapping[str, Any]],
    task_id: str,
    optimizer: CandidateOptimizer,
    apply_policy: str = "proposal",
    post_apply_evaluator: Callable[[CandidateVariant], Any] | None = None,
) -> SelfEvolveRunnerResult:
    trajectory = list(current_trajectory)
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id=task_id,
    )
    trace_pack = dataset.cases[0].trace_pack
    if trace_pack is None:
        raise ValueError("current trajectory dataset did not produce a trace pack")

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(workspace_root),
        optimizer=optimizer,
        post_apply_evaluator=post_apply_evaluator,
    )
    return await runner.run_explicit_target(
        run_id=run_id,
        target=target,
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy=apply_policy,
    )


def optimize_from_cli_request(
    *,
    workspace_root: str | Path,
    agent: str | None = None,
    task: str | None = None,
    target: str | None = None,
    dataset: str | None = None,
    from_session: str | None = None,
    from_trajectory: str | None = None,
    batch_config: str | None = None,
    current_trajectory: Iterable[Mapping[str, Any]] | None = None,
    iterations: int | None = None,
    apply_policy: str = "proposal",
    infer_target: bool = False,
) -> Mapping[str, Any]:
    if apply_policy not in {"proposal", "auto_verified"}:
        raise ValueError(f"unsupported apply policy: {apply_policy}")
    if (
        not dataset
        and not from_session
        and not from_trajectory
        and not batch_config
        and current_trajectory is None
    ):
        raise ValueError("an eval source is required")

    source_config = (
        SelfEvolveEvalSourceConfig(kind="current_trajectory")
        if current_trajectory is not None
        else _source_config_from_cli_request(
            dataset=dataset,
            from_session=from_session,
            from_trajectory=from_trajectory,
            batch_config=batch_config,
            workspace_root=workspace_root,
        )
    )
    built_dataset = build_dataset_from_source(
        source_config,
        current_trajectory=current_trajectory,
        task_id=task,
    )
    trace_packs = tuple(
        case.trace_pack for case in built_dataset.cases if case.trace_pack is not None
    )
    store = FilesystemSelfEvolveStore(workspace_root)
    target_selection_report: TargetSelectionReport | None = None
    target_provenance: TargetProvenance | None = None
    target_selection_path: Path | None = None
    target_provenance_path: Path | None = None

    if infer_target:
        if not trace_packs:
            target_selection_report = _no_evidence_target_selection_report(source_config.kind)
            run_id = _cli_run_id(
                "no_evidence",
                dataset,
                from_session,
                from_trajectory,
                batch_config,
                iterations,
            )
            return _persist_no_target_cli_result(
                store=store,
                run_id=run_id,
                dataset=built_dataset,
                target_selection_report=target_selection_report,
                apply_policy=apply_policy,
            )
        target_selection_report, inventory_entry = _infer_target_from_trace_packs(
            trace_packs,
            workspace_root=workspace_root,
        )
        target_selection_key = (
            f"{target_selection_report.selected_target.target_type}:"
            f"{target_selection_report.selected_target.target_id}"
            if target_selection_report.selected_target is not None
            else "no_target"
        )
        run_id = _cli_run_id(
            target_selection_key,
            dataset,
            from_session,
            from_trajectory,
            batch_config,
            iterations,
        )
        if target_selection_report.selected_target is None:
            return _persist_no_target_cli_result(
                store=store,
                run_id=run_id,
                dataset=built_dataset,
                target_selection_report=target_selection_report,
                apply_policy=apply_policy,
            )
        if inventory_entry is not None:
            target_provenance = inventory_entry.provenance
        try:
            target_adapter = _target_from_ref(
                target_selection_report.selected_target,
                workspace_root=workspace_root,
            )
        except NotImplementedError as exc:
            return _persist_unsupported_target_cli_result(
                store=store,
                run_id=run_id,
                dataset=built_dataset,
                target_selection_report=target_selection_report,
                target_provenance=target_provenance,
                apply_policy=apply_policy,
                reason=str(exc),
            )
    else:
        if not target:
            raise ValueError("target is required unless target inference is enabled")
        run_id = _cli_run_id(
            target,
            dataset,
            from_session,
            from_trajectory,
            batch_config,
            iterations,
        )
        target_adapter = _target_from_cli_ref(target, workspace_root=workspace_root)
        target_selection_report = _explicit_target_selection_report(
            target_adapter.identity,
            trace_packs,
        )

    async def _cli_default_mutation(prompt: str) -> dict[str, str]:
        current_content = target_adapter.load_current_content()
        candidate_content = _default_cli_skill_candidate(
            current_content=current_content,
            trace_packs=trace_packs,
        )
        return {
            "content": candidate_content,
            "rationale": (
                "Generated a trajectory-backed skill proposal through the default "
                "CLI self-evolve mutator."
                if candidate_content != current_content
                else "No trajectory evidence available; preserved proposal-only baseline."
            ),
        }

    import asyncio

    result = asyncio.run(
        SelfEvolveRunner(
            store=store,
            optimizer=TraceReflectiveLLMMutator(mutate_text=_cli_default_mutation),
        ).run_explicit_target(
            run_id=run_id,
            target=target_adapter,
            dataset=built_dataset,
            trace_packs=trace_packs,
            apply_policy=apply_policy,
            target_selection_report=target_selection_report,
            target_provenance=target_provenance,
        )
    )
    run_path = store.run_path(run_id)
    if target_selection_report is not None:
        target_selection_path = run_path / "target_selection.json"
    if target_provenance is not None:
        target_provenance_path = run_path / "target_provenance.json"

    report_path = run_path / "report.json"
    summary = {
        "report_path": str(report_path),
        "best_candidate_id": (
            result.selected_candidate.candidate_id
            if result.selected_candidate is not None
            else None
        ),
        "run_id": result.run.run_id,
        "status": result.run.status.value,
    }
    if target_selection_path is not None:
        summary["target_selection_path"] = str(target_selection_path)
    if target_provenance_path is not None:
        summary["target_provenance_path"] = str(target_provenance_path)
    return summary


def _default_cli_skill_candidate(
    *,
    current_content: str,
    trace_packs: tuple[TracePack, ...],
) -> str:
    if not trace_packs:
        return current_content

    evidence_ids = [
        step.evidence_id
        for trace_pack in trace_packs[:3]
        for step in trace_pack.steps[:4]
    ]
    task_ids = [trace_pack.task_id for trace_pack in trace_packs[:3]]
    serialized_evidence = " ".join(
        str(value).lower()
        for trace_pack in trace_packs
        for step in trace_pack.steps
        for value in (step.action, step.state, step.reward)
    )
    guidance = [
        "Use trajectory evidence before choosing or repeating tool actions.",
        (
            "When a tool path fails or repeats, record the observed failure and "
            "switch to an alternate evidence source before finalizing."
        ),
    ]
    if (
        "cdp" in serialized_evidence
        or "profile" in serialized_evidence
        or "port" in serialized_evidence
    ):
        guidance.insert(
            1,
            "For browser/CDP work, verify the active endpoint and profile before relying on page state.",
        )

    section = [
        "## Self-Evolve Trace Guidance",
        "",
        f"- Source task ids: {', '.join(task_ids)}",
        f"- Evidence steps: {', '.join(evidence_ids)}",
    ]
    section.extend(f"- {item}" for item in guidance)

    heading = "\n## Self-Evolve Trace Guidance\n"
    prefix = current_content.rstrip()
    if heading in current_content:
        prefix = current_content.split(heading, 1)[0].rstrip()
    return prefix + "\n\n" + "\n".join(section) + "\n"


def _target_from_cli_ref(target: str, *, workspace_root: str | Path) -> SelfEvolveTarget:
    target_type, _, target_id = target.partition(":")
    if target_type != "skill" or not target_id:
        raise NotImplementedError(f"CLI target adapter is not implemented for {target!r}")
    return _skill_target_from_id(target_id, workspace_root=workspace_root)


def _candidate_gate_results(
    candidate: CandidateVariant,
    *,
    current_content: str,
    workspace_root: str | Path,
    max_chars: int,
    target_provenance: TargetProvenance | None,
) -> list[GateResult]:
    results = [
        NoopCandidateGate().evaluate(current_content=current_content, candidate=candidate),
        MalformedCandidateGate().evaluate(candidate),
        TokenLimitGate(max_chars=max_chars).evaluate(candidate),
        ProtectedPathGate(workspace_root=workspace_root).evaluate(candidate),
        ExternalCodeEvolutionGate().evaluate(candidate),
    ]
    if candidate.target.target_type == "skill":
        results.append(SkillMarkdownGate().evaluate(candidate))
    if target_provenance is not None:
        results.append(TrustProvenanceGate().evaluate(target_provenance))
    return results


def _target_from_ref(
    target_ref: SelfEvolveTargetRef,
    *,
    workspace_root: str | Path,
) -> SelfEvolveTarget:
    if target_ref.target_type == "skill":
        return _skill_target_from_id(target_ref.target_id, workspace_root=workspace_root)
    raise NotImplementedError(
        "target inference selected "
        f"{target_ref.target_type}:{target_ref.target_id}, but that target adapter "
        "is not implemented for phase 1 CLI runs"
    )


def _skill_target_from_id(target_id: str, *, workspace_root: str | Path) -> SkillTextTarget:
    workspace = Path(workspace_root)
    candidates = (
        workspace / "aworld-skills" / target_id / "SKILL.md",
        workspace / "skills" / target_id / "SKILL.md",
    )
    for path in candidates:
        if path.exists():
            return SkillTextTarget(path)
    raise FileNotFoundError(f"skill target not found: skill:{target_id}")


def _infer_target_from_trace_packs(
    trace_packs: tuple[TracePack, ...],
    *,
    workspace_root: str | Path,
) -> tuple[TargetSelectionReport, TargetInventoryEntry | None]:
    if not trace_packs:
        raise ValueError("target inference requires trajectory evidence")

    inventory = build_default_target_inventory(workspace_root)
    assigner = TrajectoryCreditAssigner(inventory=inventory)
    reports = [assigner.assign(trace_pack) for trace_pack in trace_packs]
    best_report = max(
        reports,
        key=lambda item: (
            item.selected_target is not None,
            item.confidence,
        ),
    )
    if best_report.selected_target is not None:
        return best_report, inventory.find(
            best_report.selected_target.target_type,
            best_report.selected_target.target_id,
        )
    return best_report, None


def _explicit_target_selection_report(
    target: SelfEvolveTargetRef,
    trace_packs: tuple[TracePack, ...],
) -> TargetSelectionReport | None:
    if not trace_packs:
        return None
    evidence_step_ids = tuple(
        step.evidence_id
        for trace_pack in trace_packs
        for step in trace_pack.steps
    )
    return TargetSelectionReport(
        selected_target=target,
        confidence=1.0,
        evidence_step_ids=evidence_step_ids,
        failure_category="explicit_target",
        signals=("explicit_target",),
        diagnostics={
            "pack_ids": [trace_pack.pack_id for trace_pack in trace_packs],
            "target_inference": "bypassed",
        },
    )


def _no_evidence_target_selection_report(source_kind: str) -> TargetSelectionReport:
    return TargetSelectionReport(
        selected_target=None,
        confidence=0.0,
        evidence_step_ids=(),
        failure_category="no_target",
        signals=("missing_trajectory_evidence",),
        no_target_reason="target inference requires trajectory evidence",
        diagnostics={"source_kind": source_kind},
    )


def _persist_no_target_cli_result(
    *,
    store: FilesystemSelfEvolveStore,
    run_id: str,
    dataset: SelfEvolveDataset,
    target_selection_report: TargetSelectionReport,
    apply_policy: str,
) -> Mapping[str, Any]:
    target = SelfEvolveTargetRef(target_type="no_target", target_id="no_target")
    run = SelfEvolveRun(run_id=run_id, target=target, status=SelfEvolveRunStatus.REJECTED)
    store.create_run(run)
    store.write_dataset_recipe(run_id, dataset.recipe)
    target_selection_path = store.write_target_selection_report(run_id, target_selection_report)
    report_path = store.write_report(
        run_id,
        {
            "run_id": run_id,
            "target": {
                "target_type": target.target_type,
                "target_id": target.target_id,
                "path": target.path,
            },
            "apply_policy": apply_policy,
            "candidate_ids": [],
            "selected_candidate_id": None,
            "status": run.status.value,
            "target_selection": to_json_dict(target_selection_report),
        },
    )
    return {
        "report_path": str(report_path),
        "target_selection_path": str(target_selection_path),
        "best_candidate_id": None,
        "run_id": run_id,
        "status": run.status.value,
    }


def _persist_unsupported_target_cli_result(
    *,
    store: FilesystemSelfEvolveStore,
    run_id: str,
    dataset: SelfEvolveDataset,
    target_selection_report: TargetSelectionReport,
    target_provenance: TargetProvenance | None,
    apply_policy: str,
    reason: str,
) -> Mapping[str, Any]:
    if target_selection_report.selected_target is None:
        return _persist_no_target_cli_result(
            store=store,
            run_id=run_id,
            dataset=dataset,
            target_selection_report=target_selection_report,
            apply_policy=apply_policy,
        )

    target = target_selection_report.selected_target
    run = SelfEvolveRun(run_id=run_id, target=target, status=SelfEvolveRunStatus.REJECTED)
    store.create_run(run)
    store.write_dataset_recipe(run_id, dataset.recipe)
    target_selection_path = store.write_target_selection_report(run_id, target_selection_report)
    target_provenance_path = (
        store.write_target_provenance(run_id, target_provenance)
        if target_provenance is not None
        else None
    )
    report_path = store.write_report(
        run_id,
        {
            "run_id": run_id,
            "target": {
                "target_type": target.target_type,
                "target_id": target.target_id,
                "path": target.path,
            },
            "apply_policy": apply_policy,
            "candidate_ids": [],
            "selected_candidate_id": None,
            "status": run.status.value,
            "target_selection": to_json_dict(target_selection_report),
            "unsupported_target": {
                "target_ref": _target_ref_text(target),
                "reason": reason,
            },
        },
    )
    summary = {
        "report_path": str(report_path),
        "target_selection_path": str(target_selection_path),
        "best_candidate_id": None,
        "run_id": run_id,
        "status": run.status.value,
    }
    if target_provenance_path is not None:
        summary["target_provenance_path"] = str(target_provenance_path)
    return summary


def _target_ref_text(target: SelfEvolveTargetRef) -> str:
    return f"{target.target_type}:{target.target_id}"


def _cli_run_id(
    target_key: str | None,
    dataset: str | None,
    from_session: str | None,
    from_trajectory: str | None,
    batch_config: str | None,
    iterations: int | None,
) -> str:
    return (
        "cli-"
        f"{abs(hash((target_key, dataset, from_session, from_trajectory, batch_config, iterations))) % 10**12:012d}"
    )


def _source_config_from_cli_request(
    *,
    dataset: str | None,
    from_session: str | None,
    from_trajectory: str | None,
    batch_config: str | None,
    workspace_root: str | Path,
) -> SelfEvolveEvalSourceConfig:
    if dataset:
        return SelfEvolveEvalSourceConfig(kind="jsonl", path=dataset)
    if from_trajectory:
        return SelfEvolveEvalSourceConfig(kind="trajectory_log", path=from_trajectory)
    if from_session:
        return SelfEvolveEvalSourceConfig(
            kind="session",
            path=str(workspace_root),
            session_id=from_session,
        )
    if batch_config:
        return SelfEvolveEvalSourceConfig(kind="batch_config", path=batch_config)
    raise ValueError("an eval source is required")
