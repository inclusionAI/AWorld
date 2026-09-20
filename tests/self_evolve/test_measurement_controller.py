import ast
import inspect
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aworld.self_evolve.controllers import measurement as measurement_module
from aworld.self_evolve.controllers.measurement import (
    CandidateMeasurementController,
    MeasurementPlanningConfig,
    MeasurementPlanningController,
    MeasurementPlanningIdentities,
    MeasurementPlanningRequest,
    MeasurementPlanningRuntime,
    complete_measurement_usage,
    measurement_promotion_gate,
    measurement_target_resolution,
)
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.measurement import (
    EffectDirection,
    ExperimentValidityStatus,
    MeasurementEarlyStopPolicy,
    MeasurementNextAction,
    MeasurementPolicyMode,
    MeasurementSummary,
    MeasurementUsage,
    SwapAxis,
    stable_measurement_fingerprint,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SkillTextTarget
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
)


def _planning_config(
    *,
    mode: MeasurementPolicyMode = MeasurementPolicyMode.ADVISORY,
) -> MeasurementPlanningConfig:
    fingerprint = stable_measurement_fingerprint
    return MeasurementPlanningConfig(
        mode=mode,
        identities=MeasurementPlanningIdentities(
            task_model=fingerprint({"identity": "task-model"}),
            generator=fingerprint({"identity": "generator"}),
            scheduler=fingerprint({"identity": "scheduler"}),
            evaluator=fingerprint({"identity": "evaluator"}),
            runtime=fingerprint({"identity": "runtime"}),
        ),
        resume_run_id=None,
        replay_resume_dir=None,
        replay_enabled=False,
        replay_backend_available=False,
        baseline_replay_repetitions=1,
        candidate_replay_repetitions=1,
        replay_repetitions_explicit=False,
        judge_repetitions=1,
        evaluation_backend_available=False,
        minimum_independent_cases=1,
        primary_metric="task_success",
        minimum_effect=0.0,
        confidence_level=0.95,
        bootstrap_samples=200,
        early_stop_policy=MeasurementEarlyStopPolicy(),
        total_run_token_budget=None,
        per_attempt_replay_token_limit=None,
        max_run_cost_usd=None,
        max_run_wall_seconds=None,
        replay_timeout_seconds=90,
    )


def _planning_request(tmp_path):
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Demo\n\nCurrent.\n", encoding="utf-8")
    target = SkillTextTarget(skill_path, allow_auto_apply=True)
    candidate = CandidateVariant(
        candidate_id="candidate-1",
        target=target.identity,
        content="# Demo\n\nCandidate.\n",
        rationale="planning boundary",
    )
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="case-1", input="task"),),
        recipe=DatasetRecipe(
            source={"kind": "measurement-controller-test"},
            split_seed="measurement-controller-test",
            splits={
                "train": ["case-1"],
                "validation": [],
                "held_out": [],
            },
            trainable_case_ids=("case-1",),
        ),
    )
    return target, candidate, dataset


def test_measurement_usage_stays_unknown_when_any_observation_is_incomplete() -> None:
    observations = (
        SimpleNamespace(usage=MeasurementUsage(tokens=10, wall_seconds=1.0)),
        SimpleNamespace(usage=MeasurementUsage(tokens=20)),
    )

    usage = complete_measurement_usage(
        observations,
        candidate_opportunities=2,
    )

    assert usage.tokens is None
    assert usage.wall_seconds is None
    assert usage.candidate_opportunities == 2


def test_direct_measurement_target_has_explicit_resolution_authority() -> None:
    resolution = measurement_target_resolution(None)

    assert resolution.confidence == 1.0
    assert resolution.origin == "direct_target_argument"
    assert resolution.inference_bypassed is True


def test_measurement_promotion_gate_preserves_measurement_decision() -> None:
    summary = MeasurementSummary(
        experiment_id="experiment-00000000000000000000000000000000",
        mode=MeasurementPolicyMode.REQUIRED,
        swap_axis=SwapAxis.ARTIFACT,
        validity_status=ExperimentValidityStatus.VALID,
        effect_direction=EffectDirection.POSITIVE,
        effect_estimate=0.2,
        confidence_lower_bound=0.1,
        confidence_upper_bound=0.3,
        budget_normalized=True,
        promotion_eligible=True,
        decision_reason="positive effect established",
        next_action=MeasurementNextAction.PROMOTE_CANDIDATE,
        attribution_report_path=None,
        independent_case_count=2,
        comparable_pair_count=2,
        measurement_readiness_stage="minimum_independent_evidence",
    )

    gate = measurement_promotion_gate(summary)

    assert gate.passed is True
    assert gate.gate_name == "trusted_improvement_measurement"
    assert gate.details["next_action"] == "promote_candidate"


def test_measurement_controller_rejects_empty_primary_metric(tmp_path) -> None:
    with pytest.raises(ValueError, match="primary_metric"):
        CandidateMeasurementController(
            store=FilesystemSelfEvolveStore(tmp_path),
            primary_metric=" ",
            summaries={},
        )


def test_measurement_planning_freezes_dynamic_request_identity(tmp_path) -> None:
    target, candidate, dataset = _planning_request(tmp_path)
    store = FilesystemSelfEvolveStore(tmp_path)
    controller = MeasurementPlanningController(
        store=store,
        config=_planning_config(),
    )
    environment = stable_measurement_fingerprint({"environment": "run-1"})
    runtime = MeasurementPlanningRuntime(experiments={})

    result = controller.plan(
        MeasurementPlanningRequest(
            run_id="run-1",
            target=target,
            dataset=dataset,
            candidate=candidate,
            candidate_count=1,
            environment_fingerprint=environment,
            target_intent="modify_existing",
        ),
        runtime,
    )

    assert result.experiment is not None
    assert result.resumed is False
    assert result.experiment.frozen_identities.environment == environment
    assert result.experiment.frozen_identities.prompt_context == (
        stable_measurement_fingerprint(
            {
                "target_type": target.identity.target_type,
                "target_id": target.identity.target_id,
                "target_intent": "modify_existing",
                "dataset": result.experiment.frozen_identities.dataset,
            }
        )
    )
    assert runtime.experiments[("run-1", "candidate-1")] == result.experiment


def test_custom_measurement_registry_does_not_read_resume_authority(
    tmp_path,
) -> None:
    target, candidate, dataset = _planning_request(tmp_path)
    config = replace(
        _planning_config(),
        resume_run_id="prior-run",
        replay_resume_dir=str(tmp_path / "missing-replay"),
    )
    controller = MeasurementPlanningController(
        store=FilesystemSelfEvolveStore(tmp_path),
        config=config,
    )

    result = controller.plan(
        MeasurementPlanningRequest(
            run_id="screening-run",
            target=target,
            dataset=dataset,
            candidate=candidate,
            candidate_count=1,
            experiment_key=("screening", "candidate-1"),
            allow_resume=False,
        ),
        MeasurementPlanningRuntime(experiments={}),
    )

    assert result.experiment is not None
    assert result.resumed is False


def test_measurement_controller_does_not_import_runner() -> None:
    tree = ast.parse(inspect.getsource(measurement_module))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert "aworld.self_evolve.runner" not in imported_modules
