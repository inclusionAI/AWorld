from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from aworld.self_evolve.measurement import (
    ArmRole,
    ComparabilityStatus,
    ComponentIdentity,
    ControlledExperimentSpec,
    ExperimentBudget,
    FrozenIdentities,
    MeasurementObservation,
    MeasurementPolicyMode,
    MeasurementUsage,
    ObservationExecutionStatus,
    OutcomePlan,
    SamplingPlan,
    SwapAxis,
    TargetResolutionConfidence,
    TrustedMeasurementService,
    build_attribution_report,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore


def _fp(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _spec() -> ControlledExperimentSpec:
    return ControlledExperimentSpec.create(
        run_id="run-store-measurement",
        mode=MeasurementPolicyMode.SHADOW,
        swap_axis=SwapAxis.ARTIFACT,
        control=ComponentIdentity("skill:demo", _fp("baseline")),
        treatment=ComponentIdentity("candidate:demo", _fp("candidate")),
        frozen_identities=FrozenIdentities(
            task_model=_fp("task-model"),
            generator=_fp("generator"),
            scheduler=_fp("scheduler"),
            evaluator=_fp("evaluator"),
            dataset=_fp("dataset"),
            environment=_fp("environment"),
            runtime=_fp("runtime"),
            prompt_context=_fp("prompt-context"),
            budget=_fp("budget"),
        ),
        sampling=SamplingPlan(independent_case_ids=("case-1",)),
        outcomes=OutcomePlan(
            primary_metric="task_success", minimum_independent_cases=1
        ),
        budgets=ExperimentBudget(),
    )


def _observations(
    spec: ControlledExperimentSpec,
) -> tuple[MeasurementObservation, ...]:
    return tuple(
        MeasurementObservation.create(
            experiment=spec,
            arm=arm,
            case_id="case-1",
            case_fingerprint=_fp("case-1"),
            split="validation",
            repetition_index=1,
            seed=None,
            component_fingerprint=(
                spec.control.fingerprint
                if arm is ArmRole.CONTROL
                else spec.treatment.fingerprint
            ),
            execution_status=ObservationExecutionStatus.SUCCEEDED,
            comparability=ComparabilityStatus.COMPARABLE,
            task_success=(arm is ArmRole.TREATMENT),
            metrics={"score": 0.0 if arm is ArmRole.CONTROL else 1.0},
            usage=MeasurementUsage(tokens=100, wall_seconds=1.0),
            artifact_refs=(f"replay/case-1/{arm.value}.json",),
        )
        for arm in (ArmRole.CONTROL, ArmRole.TREATMENT)
    )


def test_measurement_store_round_trips_all_artifacts(tmp_path) -> None:
    store = FilesystemSelfEvolveStore(tmp_path)
    spec = _spec()
    observations = _observations(spec)

    experiment_path = store.write_measurement_experiment(spec)
    observation_path = store.append_measurement_observations(
        spec.run_id, spec.experiment_id, observations
    )
    report = build_attribution_report(
        spec,
        observations,
        target_resolution=TargetResolutionConfidence(
            confidence=1.0,
            origin="operator_explicit",
            inference_bypassed=True,
        ),
        total_usage=MeasurementUsage(tokens=200, wall_seconds=2.0),
    )
    attribution_path = store.write_measurement_attribution_report(report)

    assert experiment_path.name == "experiment.json"
    assert observation_path.name == "observations.jsonl"
    assert attribution_path.name == "attribution_report.json"
    assert store.read_measurement_experiment(spec.run_id, spec.experiment_id) == spec
    assert store.read_measurement_observations(spec.run_id, spec.experiment_id) == observations
    assert store.read_measurement_attribution_report(spec.run_id, spec.experiment_id) == report
    assert store.measurement_attribution_ref(spec.run_id, spec.experiment_id) == (
        f"experiments/{spec.experiment_id}/attribution_report.json"
    )


def test_measurement_store_observation_append_is_idempotent(tmp_path) -> None:
    store = FilesystemSelfEvolveStore(tmp_path)
    spec = _spec()
    observations = _observations(spec)
    store.write_measurement_experiment(spec)

    store.append_measurement_observations(
        spec.run_id, spec.experiment_id, observations
    )
    store.append_measurement_observations(
        spec.run_id, spec.experiment_id, observations
    )

    assert store.read_measurement_observations(
        spec.run_id, spec.experiment_id
    ) == observations


def test_measurement_store_rejects_conflicting_observation_identity(tmp_path) -> None:
    store = FilesystemSelfEvolveStore(tmp_path)
    spec = _spec()
    control = _observations(spec)[0]
    store.write_measurement_experiment(spec)
    store.append_measurement_observations(
        spec.run_id, spec.experiment_id, (control,)
    )

    with pytest.raises(ValueError, match="immutable observation"):
        store.append_measurement_observations(
            spec.run_id,
            spec.experiment_id,
            (replace(control, metrics={"score": 99.0}),),
        )


def test_measurement_store_rejects_manifest_identity_conflict(tmp_path) -> None:
    store = FilesystemSelfEvolveStore(tmp_path)
    spec = _spec()
    path = store.write_measurement_experiment(spec)
    path.write_text(path.read_text().replace("skill:demo", "skill:other"))

    with pytest.raises(ValueError):
        store.write_measurement_experiment(spec)


def test_measurement_store_rejects_symlinked_experiment_root(tmp_path) -> None:
    store = FilesystemSelfEvolveStore(tmp_path)
    spec = _spec()
    run_root = store.run_path(spec.run_id)
    run_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (run_root / "experiments").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        store.write_measurement_experiment(spec)


def test_measurement_store_partial_run_has_no_synthetic_attribution(tmp_path) -> None:
    store = FilesystemSelfEvolveStore(tmp_path)
    spec = _spec()
    store.write_measurement_experiment(spec)

    with pytest.raises(FileNotFoundError):
        store.read_measurement_attribution_report(spec.run_id, spec.experiment_id)


def test_trusted_measurement_service_plans_resumes_and_runs_idempotently(
    tmp_path,
) -> None:
    service = TrustedMeasurementService(FilesystemSelfEvolveStore(tmp_path))
    spec = _spec()
    observations = _observations(spec)

    service.plan(spec)
    partial = service.resume(spec.run_id, spec.experiment_id)
    assert partial.experiment == spec
    assert partial.observations == ()
    assert partial.attribution is None

    first = service.run(
        spec,
        observations,
        target_resolution=TargetResolutionConfidence(
            confidence=1.0,
            origin="operator_explicit",
            inference_bypassed=True,
        ),
        measurement_usage=MeasurementUsage(tokens=200, wall_seconds=2.0),
    )
    second = service.run(
        spec,
        observations,
        target_resolution=TargetResolutionConfidence(
            confidence=1.0,
            origin="operator_explicit",
            inference_bypassed=True,
        ),
        measurement_usage=MeasurementUsage(tokens=200, wall_seconds=2.0),
    )

    complete = service.inspect(spec.run_id, spec.experiment_id)
    assert first == second
    assert complete.attribution == second
    assert complete.observations == observations
