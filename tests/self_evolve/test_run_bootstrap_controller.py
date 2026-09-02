from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from aworld.self_evolve.budget import (
    BudgetCeilings,
    BudgetStage,
    BudgetUsage,
    RunBudgetLedger,
    SchedulerState,
)
from aworld.self_evolve.controllers import (
    run_bootstrap,
    run_budget_support,
    run_configuration,
    run_resources,
    run_workflow,
)
from aworld.self_evolve.controllers.run_bootstrap import (
    InitialIterationState,
    RestoredRunHistory,
    RunBootstrapPolicy,
    RunBootstrapRequest,
    RunBootstrapRuntime,
    RunBootstrapState,
    RunHistoryPolicy,
    RunHistoryRequest,
    RunHistoryRuntime,
    bootstrap_explicit_target_run,
    bootstrap_run_history,
)
from aworld.self_evolve.controllers.run_configuration import (
    ConstructedRunnerBudget,
    ConstructedRunnerControllers,
    ConstructedRunnerMeasurement,
    ConstructedRunnerMutableState,
    ConstructedRunnerPolicy,
    ConstructedRunnerReplay,
    ConstructedRunnerRuntime,
    RunnerBudgetConfiguration,
    RunnerConstructionRequest,
    RunnerMeasurementConfiguration,
    RunnerPolicyConfiguration,
    RunnerReplayConfiguration,
    RunnerRuntimeDependencies,
    build_runner_construction,
)
from aworld.self_evolve.controllers.run_resources import (
    RunBudgetContext,
    RunFailureCleanup,
)
from aworld.self_evolve.controllers.run_workflow import (
    WorkflowEstimationPolicy,
    WorkflowEstimationRequest,
    estimate_run_workflow,
)
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.measurement import (
    MeasurementEarlyStopPolicy,
    MeasurementPolicyMode,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.runner import SelfEvolveRunner
from aworld.self_evolve.run_defaults import (
    DEFAULT_CANDIDATE_GENERATION_TOKENS_PER_UNIT,
)
from aworld.self_evolve.types import DatasetRecipe, EvaluationSummary


def _construction_request(tmp_path) -> RunnerConstructionRequest:
    return RunnerConstructionRequest(
        runtime=RunnerRuntimeDependencies(
            store=FilesystemSelfEvolveStore(tmp_path),
            optimizer=SimpleNamespace(),
            post_apply_evaluator=None,
            evaluation_backend=None,
            regression_backend=None,
            regression_suites=(),
            challenger_backend=None,
            candidate_replay_backend=None,
            regression_replay_backend=None,
            runtime_registry_refresher=None,
            runtime_skill_activator=None,
            progress_callback=None,
            replay_adaptation_compiler=None,
            concurrency_policy=None,
            task_batch_executor=None,
            skill_evolution_contract=None,
            runner_type_name="SelfEvolveRunner",
        ),
        budget=RunnerBudgetConfiguration(
            max_run_tokens=None,
            total_run_token_budget=None,
            per_attempt_replay_token_limit=None,
            max_run_cost_usd=None,
            max_run_wall_seconds=None,
            candidate_generation_tokens_per_unit=32_768,
            candidate_generation_cost_usd_per_unit=Decimal("0.05"),
            candidate_generation_wall_seconds_per_unit=Decimal("120"),
            candidate_screening_tokens_per_unit=4_096,
            candidate_screening_cost_usd_per_unit=Decimal("0.05"),
            candidate_screening_wall_seconds_per_unit=Decimal("210"),
            replay_tokens_per_unit=4_096,
            replay_cost_usd_per_unit=Decimal("0.05"),
            replay_wall_seconds_per_unit=Decimal("600"),
            evaluation_tokens_per_unit=2_048,
            evaluation_cost_usd_per_unit=Decimal("0.02"),
            evaluation_wall_seconds_per_unit=Decimal("60"),
            deprecated_config_mappings=(),
        ),
        replay=RunnerReplayConfiguration(
            replay_enabled=False,
            replay_timeout_seconds=600,
            replay_total_timeout_seconds=None,
            replay_resume_dir=None,
            replay_max_steps=None,
            replay_candidate_limit=2,
            candidate_screening_max_cases=3,
            max_generated_candidates=6,
            max_full_evaluation_candidates=3,
            max_score_tiebreak_candidates=1,
            baseline_replay_repetitions=1,
            candidate_replay_repetitions=1,
            replay_repetitions_explicit=False,
            replay_stability_margin=0.0,
            replay_agent=None,
        ),
        measurement=RunnerMeasurementConfiguration(
            mode=MeasurementPolicyMode.OFF,
            primary_metric="task_success",
            minimum_effect=0.0,
            confidence_level=0.95,
            minimum_independent_cases=2,
            bootstrap_samples=2_000,
            zero_yield_patience=2,
            invalid_control_patience=2,
            maximum_interval_width=None,
            resume_run_id=None,
        ),
        policy=RunnerPolicyConfiguration(
            challenger_enabled=True,
            challenger_max_cases=3,
            min_score_delta=0.0,
            pending_duplicate=False,
            max_iterations=1,
            min_eval_cases=30,
            judge_repetitions=3,
            candidate_generation_output_tokens_per_unit=16_000,
            candidate_generation_model_name="gpt-4o",
            auto_apply_target_types=("skill",),
            allow_generated_target_mutation=False,
            allow_external_target_mutation=False,
            inferred_new_skill_policy="auto_verified",
            skip_duplicate_rejected_candidate_gate=False,
            ingestion_model_call_count=0,
        ),
    )


def test_explicit_none_generation_default_matches_facade_and_budget_admission(
    tmp_path,
) -> None:
    request = _construction_request(tmp_path)
    request = replace(
        request,
        budget=replace(
            request.budget,
            candidate_generation_tokens_per_unit=None,
            total_run_token_budget=(
                DEFAULT_CANDIDATE_GENERATION_TOKENS_PER_UNIT - 1
            ),
        ),
    )
    constructed = build_runner_construction(request)
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path / "facade"),
        optimizer=SimpleNamespace(),
        candidate_generation_tokens_per_unit=None,
    )

    assert constructed.budget.candidate_generation_tokens_per_unit == (
        DEFAULT_CANDIDATE_GENERATION_TOKENS_PER_UNIT
    )
    assert runner.candidate_generation_tokens_per_unit == (
        DEFAULT_CANDIDATE_GENERATION_TOKENS_PER_UNIT
    )
    generation_usage = constructed.budget.cold_start_by_stage[
        BudgetStage.CANDIDATE_GENERATION
    ]
    assert generation_usage == BudgetUsage(
        tokens=DEFAULT_CANDIDATE_GENERATION_TOKENS_PER_UNIT,
        cost_usd=Decimal("0.05"),
        wall_seconds=Decimal("120"),
    )

    budget_context = RunBudgetContext(
        ledger=RunBudgetLedger(
            BudgetCeilings(
                total_tokens=DEFAULT_CANDIDATE_GENERATION_TOKENS_PER_UNIT - 1,
                total_cost_usd=None,
                wall_seconds=None,
            )
        ),
        cold_start_by_stage=constructed.budget.cold_start_by_stage,
    )
    decision = budget_context.reserve(
        BudgetStage.CANDIDATE_GENERATION,
        "canonical-default-admission",
    )
    assert decision.allowed is False
    assert decision.estimate.tokens == DEFAULT_CANDIDATE_GENERATION_TOKENS_PER_UNIT


def _dataset(case_count: int = 2) -> SelfEvolveDataset:
    case_ids = tuple(f"case-{index}" for index in range(case_count))
    return SelfEvolveDataset(
        cases=tuple(EvalCase(case_id=case_id, input={}) for case_id in case_ids),
        recipe=DatasetRecipe(
            source={"kind": "bootstrap-test"},
            split_seed="seed",
            splits={"train": list(case_ids)},
            trainable_case_ids=case_ids,
        ),
    )


def _bootstrap_request(
    *,
    state: RunBootstrapState | None = None,
    policy: RunBootstrapPolicy | None = None,
    runtime_overrides: dict[str, object] | None = None,
) -> RunBootstrapRequest:
    run = SimpleNamespace(
        run_id="run-1",
        target=SimpleNamespace(identity=SimpleNamespace()),
        dataset=_dataset(1),
        campaign_prior_run_ids=("prior-1",),
        campaign_scheduler_checkpoint_run_ids=None,
    )
    runtime = RunBootstrapRuntime(
        store=SimpleNamespace(),
        optimizer=SimpleNamespace(),
        challenger_backend=None,
        candidate_replay_backend=None,
        regression_replay_backend=None,
        evaluation_backend=None,
        cold_start_by_stage={},
        screening_observation_scope_fingerprint=lambda **_: "scope",
        restore_campaign_screening_case_observations=lambda *_, **__: None,
        restore_historical_screening_lifecycle_observations=lambda *_, **__: None,
        screening_control_harness_fingerprint=lambda: "harness",
        screening_control_preflight=lambda *_, **__: {"status": "feasible"},
        backend_proves_zero_budget_usage=lambda *_: False,
        load_prior_scheduler_state=lambda *_, **__: SchedulerState(),
        candidate_generation_limit=lambda **_: 2,
        register_budget_context=lambda _: None,
    )
    if runtime_overrides:
        runtime = replace(runtime, **runtime_overrides)
    return RunBootstrapRequest(
        run=run,
        policy=policy
        or RunBootstrapPolicy(
            total_run_token_budget=None,
            max_run_cost_usd=None,
            max_run_wall_seconds=None,
            ingestion_model_call_count=0,
            replay_timeout_seconds=900,
            replay_candidate_limit=2,
            screening_timeout_ceiling_seconds=300,
        ),
        state=state
        or RunBootstrapState(
            candidate_screening_case_observations={},
            candidate_screening_control_observations={},
            candidate_screening_loaded_run_ids=set(),
            current_run_authoritative_case_observations={},
            candidate_screening_observation_dataset_fingerprint="scope",
        ),
        runtime=runtime,
    )


def test_workflow_estimation_compiles_bound_budget_item_factory() -> None:
    result = estimate_run_workflow(
        WorkflowEstimationRequest(
            dataset=_dataset(),
            apply_policy="verified_only",
            regression_suites=(),
            policy=WorkflowEstimationPolicy(
                max_iterations=2,
                replay_enabled=True,
                replay_backend_available=True,
                repetitions_explicit=True,
                minimum_independent_cases=2,
                baseline_repetitions=1,
                candidate_repetitions=2,
                evaluation_backend_available=True,
                judge_repetitions=3,
                progress_repair_extension_iterations=6,
            ),
            replayable_dataset=lambda dataset: dataset,
        )
    )

    assert result.iteration_budget == 8
    assert result.estimated_baseline_repetitions == 1
    assert result.estimated_candidate_repetitions == 2
    assert result.budget_items(iteration=3, candidate_count=2) == (
        (BudgetStage.CANDIDATE_GENERATION, "iteration-3-workflow-generation", 2),
        (BudgetStage.PAIRED_REPLAY, "iteration-3-workflow-replay", 12),
        (BudgetStage.EVALUATION, "iteration-3-workflow-evaluation", 40),
        (BudgetStage.JUDGE, "iteration-3-workflow-judge", 120),
    )


def test_run_bootstrap_owns_observation_budget_and_scheduler_initialization() -> None:
    case_observations = {"stale": {"count": 1}}
    control_observations = {"stale": {"status": "valid"}}
    loaded_run_ids = {"old-run"}
    authoritative = {"old": {"count": 1}}
    calls: list[str] = []
    store = SimpleNamespace()
    run = SimpleNamespace(
        run_id="run-1",
        target=SimpleNamespace(identity=SimpleNamespace()),
        dataset=_dataset(1),
        campaign_prior_run_ids=("prior-1",),
        campaign_scheduler_checkpoint_run_ids=None,
    )

    result = bootstrap_explicit_target_run(
        RunBootstrapRequest(
            run=run,
            policy=RunBootstrapPolicy(
                total_run_token_budget=None,
                max_run_cost_usd=None,
                max_run_wall_seconds=None,
                ingestion_model_call_count=0,
                replay_timeout_seconds=900,
                replay_candidate_limit=2,
                screening_timeout_ceiling_seconds=300,
            ),
            state=RunBootstrapState(
                candidate_screening_case_observations=case_observations,
                candidate_screening_control_observations=control_observations,
                candidate_screening_loaded_run_ids=loaded_run_ids,
                current_run_authoritative_case_observations=authoritative,
                candidate_screening_observation_dataset_fingerprint="old-scope",
            ),
            runtime=RunBootstrapRuntime(
                store=store,
                optimizer=SimpleNamespace(),
                challenger_backend=None,
                candidate_replay_backend=None,
                regression_replay_backend=None,
                evaluation_backend=None,
                cold_start_by_stage={},
                screening_observation_scope_fingerprint=lambda **_: "new-scope",
                restore_campaign_screening_case_observations=lambda *_,
                **__: calls.append("campaign"),
                restore_historical_screening_lifecycle_observations=lambda *_,
                **__: calls.append("history"),
                screening_control_harness_fingerprint=lambda: "harness",
                screening_control_preflight=lambda *_, **kwargs: {
                    "status": "feasible",
                    "timeout": kwargs["timeout_ceiling_seconds"],
                },
                backend_proves_zero_budget_usage=lambda *_: False,
                load_prior_scheduler_state=lambda *_, **__: SchedulerState(),
                candidate_generation_limit=lambda **_: 2,
                register_budget_context=lambda _: None,
            ),
        )
    )

    assert calls == ["campaign", "history"]
    assert case_observations == {}
    assert control_observations == {}
    assert loaded_run_ids == set()
    assert authoritative == {}
    assert result.screening_control_preflight["timeout"] == 300
    assert result.scheduler_state == SchedulerState()
    assert result.budget_context.ledger.ceilings.is_unbounded


class _StartupFailure(RuntimeError):
    pass


def test_ingestion_debit_failure_releases_registered_budget_without_masking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup = RunFailureCleanup()
    registered: list[RunBudgetContext] = []
    failure = _StartupFailure("ingestion debit failed")

    def register(context: RunBudgetContext) -> None:
        registered.append(context)
        cleanup.register_budget_context(context)

    def fail_debit(*_: object, **__: object) -> None:
        raise failure

    monkeypatch.setattr(RunBudgetContext, "debit", fail_debit)
    request = _bootstrap_request(
        policy=RunBootstrapPolicy(
            total_run_token_budget=10_000,
            max_run_cost_usd=None,
            max_run_wall_seconds=None,
            ingestion_model_call_count=1,
            replay_timeout_seconds=900,
            replay_candidate_limit=2,
            screening_timeout_ceiling_seconds=300,
        ),
        runtime_overrides={
            "cold_start_by_stage": {
                BudgetStage.CANDIDATE_GENERATION: BudgetUsage(tokens=1)
            },
            "register_budget_context": register,
        },
    )

    with pytest.raises(_StartupFailure) as captured:
        try:
            bootstrap_explicit_target_run(request)
        except _StartupFailure:
            cleanup.cleanup()
            raise

    assert captured.value is failure
    assert len(registered) == 1
    assert registered[0].ledger.outstanding_reservations == ()
    assert registered[0].releases[-1]["reason_code"] == (
        "run_unhandled_exception_cleanup"
    )


def test_scheduler_restore_failure_releases_registered_budget_without_masking() -> None:
    cleanup = RunFailureCleanup()
    registered: list[RunBudgetContext] = []
    failure = _StartupFailure("scheduler restore failed")

    def register_with_reservation(context: RunBudgetContext) -> None:
        registered.append(context)
        cleanup.register_budget_context(context)
        decision = context.reserve(BudgetStage.CANDIDATE_GENERATION, "pending")
        assert decision.allowed is True

    def fail_scheduler(*_: object, **__: object) -> SchedulerState:
        raise failure

    request = _bootstrap_request(
        policy=RunBootstrapPolicy(
            total_run_token_budget=100,
            max_run_cost_usd=None,
            max_run_wall_seconds=None,
            ingestion_model_call_count=0,
            replay_timeout_seconds=900,
            replay_candidate_limit=2,
            screening_timeout_ceiling_seconds=300,
        ),
        runtime_overrides={
            "cold_start_by_stage": {
                BudgetStage.CANDIDATE_GENERATION: BudgetUsage(tokens=1)
            },
            "register_budget_context": register_with_reservation,
            "load_prior_scheduler_state": fail_scheduler,
        },
    )

    with pytest.raises(_StartupFailure) as captured:
        try:
            bootstrap_explicit_target_run(request)
        except _StartupFailure:
            cleanup.cleanup()
            raise

    assert captured.value is failure
    assert len(registered) == 1
    assert registered[0].ledger.outstanding_reservations == ()


@pytest.mark.parametrize("failure_stage", ("campaign", "history", "preflight"))
def test_observation_recovery_is_atomic_and_retry_consistent(
    failure_stage: str,
) -> None:
    state = RunBootstrapState(
        candidate_screening_case_observations={"stable": {"count": 1}},
        candidate_screening_control_observations={"stable": {"status": "valid"}},
        candidate_screening_loaded_run_ids={"stable-run"},
        current_run_authoritative_case_observations={"stable": {"count": 2}},
        candidate_screening_observation_dataset_fingerprint="scope",
    )
    failure = _StartupFailure(f"{failure_stage} failed")

    def campaign(
        observations: dict[str, object],
        *,
        loaded_run_ids: set[str],
        control_observations: dict[str, object],
        **__: object,
    ) -> None:
        observations["campaign-partial"] = {"count": 1}
        control_observations["campaign-partial"] = {"status": "valid"}
        loaded_run_ids.add("campaign-partial")
        if failure_stage == "campaign":
            raise failure

    def history(
        observations: dict[str, object],
        *,
        loaded_run_ids: set[str],
        control_observations: dict[str, object],
        **__: object,
    ) -> None:
        observations["history-partial"] = {"count": 1}
        control_observations["history-partial"] = {"status": "valid"}
        loaded_run_ids.add("history-partial")
        if failure_stage == "history":
            raise failure

    def preflight(
        _: SelfEvolveDataset,
        *,
        observations: dict[str, object],
        **__: object,
    ) -> dict[str, object]:
        observations["preflight-partial"] = {"count": 1}
        if failure_stage == "preflight":
            raise failure
        return {"status": "feasible"}

    request = _bootstrap_request(
        state=state,
        runtime_overrides={
            "restore_campaign_screening_case_observations": campaign,
            "restore_historical_screening_lifecycle_observations": history,
            "screening_control_preflight": preflight,
        },
    )
    with pytest.raises(_StartupFailure) as captured:
        bootstrap_explicit_target_run(request)
    assert captured.value is failure
    assert state.candidate_screening_case_observations == {"stable": {"count": 1}}
    assert state.candidate_screening_control_observations == {
        "stable": {"status": "valid"}
    }
    assert state.candidate_screening_loaded_run_ids == {"stable-run"}
    assert state.current_run_authoritative_case_observations == {"stable": {"count": 2}}

    retry_loaded_ids: list[set[str]] = []

    def retry_campaign(
        observations: dict[str, object],
        *,
        loaded_run_ids: set[str],
        **__: object,
    ) -> None:
        retry_loaded_ids.append(set(loaded_run_ids))
        observations["campaign"] = {"count": 1}
        loaded_run_ids.add("campaign")

    retry = replace(
        request,
        runtime=replace(
            request.runtime,
            restore_campaign_screening_case_observations=retry_campaign,
            restore_historical_screening_lifecycle_observations=(
                lambda observations, **__: observations.update(
                    {"history": {"count": 1}}
                )
            ),
            screening_control_preflight=lambda *_, **__: {"status": "feasible"},
        ),
    )
    bootstrap_explicit_target_run(retry)

    assert retry_loaded_ids == [{"stable-run"}]
    assert state.candidate_screening_loaded_run_ids == {
        "stable-run",
        "campaign",
    }
    assert state.current_run_authoritative_case_observations == {}


def test_runner_construction_validates_before_building_services(tmp_path) -> None:
    request = _construction_request(tmp_path)
    invalid = replace(
        request,
        policy=replace(
            request.policy,
            candidate_generation_output_tokens_per_unit=0,
        ),
    )

    with pytest.raises(
        ValueError,
        match="candidate_generation_output_tokens_per_unit must be positive",
    ):
        build_runner_construction(invalid)


def test_runner_construction_preserves_legacy_budget_mapping_and_typed_bundles(
    tmp_path,
) -> None:
    request = _construction_request(tmp_path)
    request = replace(
        request,
        budget=replace(
            request.budget,
            max_run_tokens=77,
            deprecated_config_mappings=("existing_mapping",),
        ),
    )

    result = build_runner_construction(request)

    assert isinstance(result.runtime, ConstructedRunnerRuntime)
    assert isinstance(result.budget, ConstructedRunnerBudget)
    assert isinstance(result.policy, ConstructedRunnerPolicy)
    assert isinstance(result.replay, ConstructedRunnerReplay)
    assert isinstance(result.measurement, ConstructedRunnerMeasurement)
    assert isinstance(result.controllers, ConstructedRunnerControllers)
    assert isinstance(result.mutable, ConstructedRunnerMutableState)
    assert result.budget.total_run_token_budget == 77
    assert result.budget.per_attempt_replay_token_limit == 77
    assert result.budget.deprecated_config_mappings == (
        "existing_mapping",
        "max_run_tokens_to_total_run_token_budget",
        "max_run_tokens_to_per_attempt_replay_token_limit",
    )
    assert not hasattr(result, "attributes")


def test_history_bootstrap_restores_authoritative_accepted_and_rejected_sets(
    tmp_path,
) -> None:
    rejected = EvaluationSummary(
        variant_id="rejected",
        metrics={"candidate_status": "rejected"},
        dataset_split="validation",
    )
    accepted = EvaluationSummary(
        variant_id="accepted",
        metrics={
            "candidate_status": "accepted",
            "publication_completed": True,
        },
        dataset_split="validation",
    )
    advisory = EvaluationSummary(
        variant_id="advisory",
        metrics={"candidate_status": "rejected", "advisory": True},
        dataset_split="validation",
    )
    package_candidates: list[set[str]] = []
    writes: list[tuple[str, object]] = []
    store = SimpleNamespace(
        workspace_root=tmp_path,
        write_replay_requirements=lambda run_id, report: writes.append(
            (run_id, report)
        ),
    )
    preflight = SimpleNamespace(status="ready")
    request = RunHistoryRequest(
        run_id="run-1",
        target=SimpleNamespace(
            identity=SimpleNamespace(target_type="skill", target_id="demo")
        ),
        dataset=_dataset(1),
        trace_packs=(),
        apply_policy="verified_only",
        campaign_prior_run_ids=("prior",),
        screening_control_preflight={"candidate_generation_allowed": True},
        policy=RunHistoryPolicy(
            min_score_delta=0.0,
            min_eval_cases=1,
            judge_repetitions=1,
            candidate_screening_max_cases=1,
            max_generated_candidates=2,
            max_full_evaluation_candidates=1,
            max_score_tiebreak_candidates=0,
            replay_enabled=False,
            baseline_replay_repetitions=1,
            candidate_replay_repetitions=1,
            replay_stability_margin=0.0,
            replay_timeout_seconds=600,
            replay_total_timeout_seconds=None,
            measurement_mode="off",
            measurement_primary_metric="task_success",
            measurement_minimum_effect=0.0,
            measurement_confidence_level=0.95,
            measurement_min_independent_cases=1,
            measurement_early_stop_policy=MeasurementEarlyStopPolicy(),
        ),
        runtime=RunHistoryRuntime(
            store=store,
            replay_adaptation_compiler=SimpleNamespace(preflight=lambda **_: preflight),
            load_prior_rejected_feedback=lambda *_, **__: (
                rejected,
                accepted,
                advisory,
            ),
            extract_lesson_records=lambda *_, **__: (),
            non_authoritative_candidate_rejection=lambda metrics: bool(
                metrics.get("advisory")
            ),
            load_prior_candidate_package_index=(
                lambda *_, candidate_ids, **__: (
                    package_candidates.append(set(candidate_ids))
                    or ({"fingerprint": "accepted"}, {"accepted": "fingerprint"})
                )
            ),
            load_prior_rejected_semantic_lesson_fingerprints=lambda *_, **__: {
                "semantic-rejection"
            },
            replayable_user_task_dataset=lambda dataset: dataset,
            target_package_inventory=lambda _: ("SKILL.md",),
            target_package_sources=lambda *_, **__: {"SKILL.md": {"content": "# Demo"}},
            is_verified_apply_policy=lambda policy: policy == "verified_only",
        ),
    )

    result = bootstrap_run_history(request)

    assert isinstance(result.restored, RestoredRunHistory)
    assert isinstance(result.iteration, InitialIterationState)
    assert result.restored.rejected_candidate_ids == {"rejected"}
    assert result.restored.accepted_candidate_ids == {"accepted"}
    assert package_candidates == [{"rejected", "accepted"}]
    assert result.iteration.run_state.rejected_candidate_ids == {"rejected"}
    assert result.iteration.run_state.accepted_candidate_ids == {"accepted"}
    assert result.repair_reserved_slot_count == 1
    assert writes == [("run-1", preflight)]


def test_runner_construction_and_bootstrap_modules_never_reverse_import_runner() -> (
    None
):
    for module in (
        run_bootstrap,
        run_budget_support,
        run_configuration,
        run_resources,
        run_workflow,
    ):
        imported_modules = {
            node.module
            for node in ast.walk(ast.parse(inspect.getsource(module)))
            if isinstance(node, ast.ImportFrom)
        }
        assert "aworld.self_evolve.runner" not in imported_modules
