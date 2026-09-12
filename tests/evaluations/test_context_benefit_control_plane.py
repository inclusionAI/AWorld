from __future__ import annotations

from dataclasses import replace

import pytest

from aworld.core.context.compiler import (
    CanaryHealthEvidence,
    CanaryHealthPolicy,
    CanaryHealthStatus,
    ContextCompilerMode,
    ReadinessStatus,
    RollbackBundle,
    RolloutCapability,
    RolloutCohortPolicy,
    assess_default_on_readiness,
    assess_canary_health,
    assign_rollout_mode,
    canonical_json_hash,
)
from aworld.evaluations.context_benefit import (
    ContextAblationComponent,
    ContextAblationContrast,
    ContextAblationPlan,
    ContextEvaluationManifest,
    ContextTrialEvidence,
    ContextVariant,
    PairedContextDelta,
    TrialFidelity,
    build_paired_deltas,
    summarize_context_benefit,
    summarize_stratified_context_benefit,
)


HASH = "sha256:" + ("a" * 64)


def _manifest():
    baseline = ContextVariant.build(
        "baseline", {"context_compiler": {"mode": "observe"}}
    )
    candidate = ContextVariant.build(
        "candidate",
        {
            "context_compiler": {
                "mode": "enforce",
                "artifact_offload": True,
                "progressive_tools": True,
                "progressive_tool_base_tools": ["read_file"],
            }
        },
    )
    return ContextEvaluationManifest.build(
        experiment_id="context-v1",
        workload_id="workload",
        workload_kind="tool",
        dataset_checksum=HASH,
        repository_snapshot="commit:abc",
        environment_hash=HASH,
        inference_profile_hash=HASH,
        variants=(baseline, candidate),
        case_ids=("case",),
        repeats=1,
        interleaving_seed=7,
        independent_verifier_id="verifier-v1",
    )


def _trial(manifest, variant: str, reward: float, tokens: int):
    return ContextTrialEvidence(
        manifest_hash=manifest.manifest_hash,
        case_id="case",
        repeat=0,
        variant=variant,
        request_hash=HASH,
        trace_hash=HASH,
        trajectory_checksum=HASH,
        artifact_checksum=None,
        verifier_result_hash=HASH,
        reward=reward,
        fidelity=TrialFidelity.COMPLETE,
        metrics={"input_tokens": tokens},
    )


def _healthy_canary(rollback):
    return assess_canary_health(
        policy=CanaryHealthPolicy(
            policy_version="health-v1",
            minimum_shadow_calls=1,
            minimum_enforce_sessions=1,
            minimum_baseline_provider_attempts=10,
            minimum_enforce_provider_attempts=10,
            max_provider_error_rate_delta=0.02,
        ),
        evidence=CanaryHealthEvidence(
            shadow_call_count=10,
            shadow_request_trace_match_count=10,
            shadow_provider_attribution_complete_count=10,
            enforce_session_count=2,
            enforce_provider_attempt_count=10,
            enforce_provider_error_count=0,
            baseline_provider_error_rate=0.0,
            security_violation_count=0,
            trajectory_incomplete_count=0,
            quality_regression=False,
            baseline_provider_attempt_count=10,
            baseline_provider_error_count=0,
            enforce_sessions_with_provider_attempt_count=2,
        ),
        rollback_bundle=rollback,
    )


def test_stratified_benefit_preserves_each_frozen_workload():
    def delta(case_id: str, cost: float) -> PairedContextDelta:
        return PairedContextDelta(
            case_id=case_id,
            repeat=0,
            baseline_variant="baseline",
            candidate_variant="candidate",
            reward_delta=0.0,
            metric_deltas={"normalized_cost_microunits": cost},
        )

    strata = (
        (delta("terminal-1", -100.0), delta("terminal-2", -100.0)),
        (delta("skills-1", 10.0),),
    )
    first = summarize_stratified_context_benefit(strata, bootstrap_samples=200, seed=17)
    second = summarize_stratified_context_benefit(
        strata, bootstrap_samples=200, seed=17
    )

    assert first == second
    assert first.complete_pairs == 3
    assert first.metric_means["normalized_cost_microunits"] == pytest.approx(
        -190.0 / 3.0
    )
    # The single SkillsBench observation is present in every draw rather than
    # disappearing when a heterogeneous portfolio is resampled as one bag.
    assert first.metric_intervals["normalized_cost_microunits"]["upper"] < 0


def test_context_only_manifest_and_paired_benefit_are_deterministic():
    manifest = _manifest()
    trials = (
        _trial(manifest, "baseline", 0.0, 1000),
        _trial(manifest, "candidate", 1.0, 600),
    )
    deltas = build_paired_deltas(
        trials, baseline_variant="baseline", candidate_variant="candidate"
    )
    first = summarize_context_benefit(deltas, bootstrap_samples=200, seed=13)
    second = summarize_context_benefit(deltas, bootstrap_samples=200, seed=13)
    assert first == second
    assert first.mean_reward_delta == 1.0
    assert first.metric_means["input_tokens"] == -400.0

    policy_a = ContextEvaluationManifest.build(
        experiment_id="context-v1",
        workload_id="workload",
        workload_kind="tool",
        dataset_checksum=HASH,
        repository_snapshot="commit:abc",
        environment_hash=HASH,
        inference_profile_hash=HASH,
        variants=manifest.variants,
        case_ids=("case",),
        repeats=1,
        interleaving_seed=7,
        independent_verifier_id="verifier-v1",
        cost_policy_hash="sha256:" + "b" * 64,
    )
    policy_b = ContextEvaluationManifest.build(
        experiment_id="context-v1",
        workload_id="workload",
        workload_kind="tool",
        dataset_checksum=HASH,
        repository_snapshot="commit:abc",
        environment_hash=HASH,
        inference_profile_hash=HASH,
        variants=manifest.variants,
        case_ids=("case",),
        repeats=1,
        interleaving_seed=7,
        independent_verifier_id="verifier-v1",
        cost_policy_hash="sha256:" + "c" * 64,
    )
    assert policy_a.manifest_hash != policy_b.manifest_hash

    with pytest.raises(ValueError, match="prompts"):
        ContextVariant.build("invalid", {"system_prompt": "benchmark hint"})

    docker_variant = ContextVariant.build(
        "docker-context",
        {
            "agent_memory_config": {"tool_result_offload": True},
            "docker_output_policy": {"max_inline_output_bytes": 8192},
        },
    )
    assert docker_variant.settings["agent_memory_config"]["tool_result_offload"] is True


def test_cache_ablation_is_provider_neutral_and_single_component():
    baseline = ContextVariant.build(
        "adaptive-cache-off",
        {
            "context_cache": {
                "enabled": False,
                "allow_provider_native_cache": False,
            }
        },
    )
    candidate = ContextVariant.build(
        "adaptive-cache-on",
        {
            "context_cache": {
                "enabled": True,
                "allow_provider_native_cache": True,
            }
        },
    )

    contrast = ContextAblationContrast.build(
        baseline=baseline,
        candidate=candidate,
        component=ContextAblationComponent.CACHE,
    )

    assert contrast.changed_paths == (
        "context_cache.allow_provider_native_cache",
        "context_cache.enabled",
    )
    assert all(
        "provider" not in path or path.endswith("native_cache")
        for path in contrast.changed_paths
    )


@pytest.mark.parametrize(
    "context_cache,error",
    [
        ({"enabled": 1}, "must be a boolean"),
        ({"allow_provider_native_cache": "yes"}, "must be a boolean"),
        ({"provider_cache_namespace": ""}, "non-empty string"),
        ({"model_name": "glm"}, "unknown fields"),
    ],
)
def test_cache_variant_rejects_invalid_or_model_specific_fields(context_cache, error):
    with pytest.raises((TypeError, ValueError), match=error):
        ContextVariant.build("invalid-cache", {"context_cache": context_cache})


def test_paired_delta_uses_candidate_upper_against_baseline_lower_cost_bound():
    manifest = _manifest()

    def bounded_trial(variant: str, *, lower: int, upper: int):
        trial = _trial(manifest, variant, 1.0, 100)
        return ContextTrialEvidence(
            manifest_hash=trial.manifest_hash,
            case_id=trial.case_id,
            repeat=trial.repeat,
            variant=trial.variant,
            request_hash=trial.request_hash,
            trace_hash=trial.trace_hash,
            trajectory_checksum=trial.trajectory_checksum,
            artifact_checksum=trial.artifact_checksum,
            verifier_result_hash=trial.verifier_result_hash,
            reward=trial.reward,
            fidelity=trial.fidelity,
            metrics={
                "normalized_cost_lower_bound_microunits": lower,
                "normalized_cost_upper_bound_microunits": upper,
            },
        )

    delta = build_paired_deltas(
        (
            bounded_trial("baseline", lower=100, upper=180),
            bounded_trial("candidate", lower=40, upper=90),
        ),
        baseline_variant="baseline",
        candidate_variant="candidate",
    )[0]

    assert delta.metric_deltas["normalized_cost_conservative_delta_microunits"] == -10


def test_context_variant_accepts_runtime_checkpoint_policy_and_ablation_is_single_component():
    baseline = ContextVariant.build(
        "compiler-core",
        {
            "context_compiler": {
                "mode": "enforce",
                "universal_final": True,
                "checkpoint_policy": "explicit",
                "destructive_sandbox_checkpoint": False,
            }
        },
    )
    adaptive = ContextVariant.build(
        "compiler-adaptive",
        {
            "context_compiler": {
                "mode": "enforce",
                "universal_final": True,
                "checkpoint_policy": "adaptive",
                "destructive_sandbox_checkpoint": True,
            }
        },
    )
    contrast = ContextAblationContrast.build(
        baseline=baseline,
        candidate=adaptive,
        component=ContextAblationComponent.ADAPTIVE_CHECKPOINT,
    )
    plan = ContextAblationPlan.build(
        name="context-components-v1",
        variants=(baseline, adaptive),
        contrasts=(contrast,),
    )

    assert contrast.changed_paths == (
        "context_compiler.checkpoint_policy",
        "context_compiler.destructive_sandbox_checkpoint",
    )
    assert plan.plan_hash.startswith("sha256:")


def test_context_variant_validates_elastic_step_budget_as_framework_policy():
    variant = ContextVariant.build(
        "elastic",
        {
            "context_compiler": {
                "elastic_step_budget": True,
                "step_budget_extension_steps": 40,
                "step_budget_hard_limit": 240,
                "step_budget_recent_progress_window": 20,
            }
        },
    )
    assert variant.settings["context_compiler"]["step_budget_hard_limit"] == 240

    with pytest.raises(TypeError, match="elastic_step_budget must be a boolean"):
        ContextVariant.build(
            "bad-elastic-flag",
            {"context_compiler": {"elastic_step_budget": 1}},
        )
    with pytest.raises(ValueError, match="require elastic_step_budget=true"):
        ContextVariant.build(
            "ignored-elastic-parameter",
            {"context_compiler": {"step_budget_extension_steps": 40}},
        )
    for field, value in (
        ("step_budget_extension_steps", True),
        ("step_budget_hard_limit", 0),
        ("step_budget_recent_progress_window", "20"),
    ):
        with pytest.raises(ValueError, match=f"{field} must be a positive integer"):
            ContextVariant.build(
                f"bad-{field}",
                {
                    "context_compiler": {
                        "elastic_step_budget": True,
                        field: value,
                    }
                },
            )


def test_ablation_rejects_cross_component_and_undeclared_variant_changes():
    baseline = ContextVariant.build(
        "baseline",
        {
            "context_compiler": {
                "mode": "enforce",
                "checkpoint_policy": "explicit",
                "completion_contract": "off",
            }
        },
    )
    mixed = ContextVariant.build(
        "mixed",
        {
            "context_compiler": {
                "mode": "enforce",
                "checkpoint_policy": "adaptive",
                "completion_contract": "enforce",
            }
        },
    )

    with pytest.raises(ValueError, match="changes fields outside"):
        ContextAblationContrast.build(
            baseline=baseline,
            candidate=mixed,
            component=ContextAblationComponent.ADAPTIVE_CHECKPOINT,
        )

    adaptive = ContextVariant.build(
        "adaptive",
        {
            "context_compiler": {
                "mode": "enforce",
                "checkpoint_policy": "adaptive",
                "completion_contract": "off",
            }
        },
    )
    contrast = ContextAblationContrast.build(
        baseline=baseline,
        candidate=adaptive,
        component=ContextAblationComponent.ADAPTIVE_CHECKPOINT,
    )
    with pytest.raises(ValueError, match="references an undeclared variant"):
        ContextAblationPlan.build(
            name="bad-plan",
            variants=(baseline, mixed),
            contrasts=(contrast,),
        )


def test_canary_assignment_falls_back_and_readiness_requires_cross_workload():
    policy = RolloutCohortPolicy(
        policy_version="v1",
        enforce_basis_points=10000,
        shadow_basis_points=0,
        salt="stable-salt",
    )
    incomplete = RolloutCapability(
        provider="openai",
        entry_point="cli",
        provider_lowering=False,
        request_trace_match=True,
        lifecycle=True,
        trajectory_complete=True,
    )
    first = assign_rollout_mode(
        session_id="session-1", policy=policy, capability=incomplete
    )
    second = assign_rollout_mode(
        session_id="session-1", policy=policy, capability=incomplete
    )
    assert first == second
    assert first.requested_mode is ContextCompilerMode.ENFORCE
    assert first.effective_mode is ContextCompilerMode.SHADOW

    rollback = RollbackBundle.build(
        previous_mode=ContextCompilerMode.SHADOW,
        previous_config={"mode": "shadow"},
        provider_capability_hash=canonical_json_hash({"openai": True}),
    )
    assert (
        RollbackBundle.from_dict(
            {
                "previous_mode": rollback.previous_mode.value,
                "previous_config": {"mode": "shadow"},
                "provider_capability_hash": rollback.provider_capability_hash,
                "bundle_hash": rollback.bundle_hash,
            }
        )
        == rollback
    )
    with pytest.raises(ValueError, match="hash mismatch"):
        RollbackBundle.from_dict(
            {
                "previous_mode": rollback.previous_mode.value,
                "previous_config": {"mode": "shadow"},
                "provider_capability_hash": rollback.provider_capability_hash,
                "bundle_hash": "sha256:" + "0" * 64,
            }
        )
    ready_capability = RolloutCapability(
        provider="openai",
        entry_point="cli",
        provider_lowering=True,
        request_trace_match=True,
        lifecycle=True,
        trajectory_complete=True,
    )
    not_ready = assess_default_on_readiness(
        capabilities=(ready_capability,),
        required_capabilities=(("openai", "cli", "sync"),),
        workload_kinds=("terminal",),
        complete_pairs=10,
        quality_regression=False,
        request_trace_match_rate=1.0,
        trajectory_complete_rate=1.0,
        rollback_config_hash=rollback.bundle_hash,
    )
    assert not_ready.status is ReadinessStatus.NOT_READY
    assert "cross_workload_evidence_missing" in not_ready.gate_failures

    healthy_canary = _healthy_canary(rollback)
    self_declared = assess_default_on_readiness(
        capabilities=(ready_capability,),
        required_capabilities=(("openai", "cli", "sync"),),
        workload_kinds=("terminal", "research"),
        complete_pairs=10,
        quality_regression=False,
        request_trace_match_rate=1.0,
        trajectory_complete_rate=1.0,
        rollback_config_hash=rollback.bundle_hash,
        canary_health_decision=healthy_canary,
        required_canary_policy_fingerprint=healthy_canary.policy_fingerprint,
    )
    assert self_declared.status is ReadinessStatus.NOT_READY
    assert "capability_matrix_incomplete" in self_declared.gate_failures

    legacy_pair = assess_default_on_readiness(
        capabilities=(ready_capability,),
        required_capabilities=(("openai", "cli"),),
        workload_kinds=("terminal", "research"),
        complete_pairs=10,
        quality_regression=False,
        request_trace_match_rate=1.0,
        trajectory_complete_rate=1.0,
        rollback_config_hash=rollback.bundle_hash,
    )
    assert "capability_matrix_incomplete" in legacy_pair.gate_failures

    missing_entry_point = assess_default_on_readiness(
        capabilities=(ready_capability,),
        required_capabilities=(
            ("openai", "agent", "sync"),
            ("openai", "cli", "sync"),
        ),
        workload_kinds=("terminal", "research"),
        complete_pairs=10,
        quality_regression=False,
        request_trace_match_rate=1.0,
        trajectory_complete_rate=1.0,
        rollback_config_hash=rollback.bundle_hash,
    )
    assert missing_entry_point.status is ReadinessStatus.NOT_READY
    assert "capability_matrix_incomplete" in missing_entry_point.gate_failures

    too_small = assess_default_on_readiness(
        capabilities=(ready_capability,),
        required_capabilities=(("openai", "cli", "sync"),),
        workload_kinds=("terminal", "research"),
        complete_pairs=2,
        quality_regression=False,
        request_trace_match_rate=1.0,
        trajectory_complete_rate=1.0,
        rollback_config_hash=rollback.bundle_hash,
    )
    assert too_small.status is ReadinessStatus.NOT_READY
    assert "insufficient_paired_evidence" in too_small.gate_failures


def test_canary_health_distinguishes_hold_continue_and_rollback():
    policy = CanaryHealthPolicy(
        policy_version="health-v1",
        minimum_shadow_calls=100,
        minimum_enforce_sessions=20,
        max_provider_error_rate_delta=0.02,
    )
    rollback = RollbackBundle.build(
        previous_mode=ContextCompilerMode.SHADOW,
        previous_config={"mode": "shadow"},
        provider_capability_hash=canonical_json_hash({"openai": True}),
    )

    hold_evidence = CanaryHealthEvidence(
        shadow_call_count=10,
        shadow_request_trace_match_count=10,
        shadow_provider_attribution_complete_count=10,
        enforce_session_count=0,
        enforce_provider_attempt_count=0,
        enforce_provider_error_count=0,
        baseline_provider_error_rate=0.01,
        security_violation_count=0,
        trajectory_incomplete_count=0,
        quality_regression=False,
        baseline_provider_attempt_count=100,
        baseline_provider_error_count=1,
        enforce_sessions_with_provider_attempt_count=0,
    )
    hold = assess_canary_health(
        policy=policy, evidence=hold_evidence, rollback_bundle=rollback
    )
    assert hold.status is CanaryHealthStatus.HOLD
    assert set(hold.reason_codes) == {
        "shadow_sample_incomplete",
        "enforce_sample_incomplete",
        "enforce_provider_attempt_sample_incomplete",
    }

    healthy = CanaryHealthEvidence(
        shadow_call_count=100,
        shadow_request_trace_match_count=100,
        shadow_provider_attribution_complete_count=100,
        enforce_session_count=20,
        enforce_provider_attempt_count=100,
        enforce_provider_error_count=2,
        baseline_provider_error_rate=0.01,
        security_violation_count=0,
        trajectory_incomplete_count=0,
        quality_regression=False,
        baseline_provider_attempt_count=100,
        baseline_provider_error_count=1,
        enforce_sessions_with_provider_attempt_count=20,
    )
    continued = assess_canary_health(
        policy=policy, evidence=healthy, rollback_bundle=rollback
    )
    assert continued.status is CanaryHealthStatus.CONTINUE
    assert continued.reason_codes == ()

    unhealthy = replace(
        healthy,
        security_violation_count=1,
        trajectory_incomplete_count=1,
    )
    rolled_back = assess_canary_health(
        policy=policy, evidence=unhealthy, rollback_bundle=rollback
    )
    assert rolled_back.status is CanaryHealthStatus.ROLLBACK_REQUIRED
    assert rolled_back.rollback_bundle_hash == rollback.bundle_hash
    assert set(rolled_back.reason_codes) == {
        "security_violation",
        "trajectory_fidelity_incomplete",
    }

    no_provider_truth = replace(
        healthy,
        enforce_provider_attempt_count=0,
        enforce_provider_error_count=0,
        enforce_sessions_with_provider_attempt_count=0,
    )
    held_without_provider_truth = assess_canary_health(
        policy=policy,
        evidence=no_provider_truth,
        rollback_bundle=rollback,
    )
    assert held_without_provider_truth.status is CanaryHealthStatus.HOLD
    assert set(held_without_provider_truth.reason_codes) == {
        "enforce_provider_attempt_sample_incomplete",
        "enforce_session_provider_coverage_incomplete",
    }

    healthy_without_rollback = assess_canary_health(
        policy=policy,
        evidence=healthy,
        rollback_bundle=None,
    )
    assert healthy_without_rollback.status is CanaryHealthStatus.HOLD
    assert healthy_without_rollback.reason_codes == ("rollback_bundle_missing",)

    with pytest.raises(ValueError, match="fingerprint mismatch"):
        replace(continued, status=CanaryHealthStatus.ROLLBACK_REQUIRED)


def test_default_on_readiness_consumes_canary_health_and_rejects_self_declared_capability():
    rollback = RollbackBundle.build(
        previous_mode=ContextCompilerMode.SHADOW,
        previous_config={"mode": "shadow"},
        provider_capability_hash=canonical_json_hash({"openai": True}),
    )
    capability = RolloutCapability(
        provider="openai",
        entry_point="agent",
        provider_lowering=True,
        request_trace_match=True,
        lifecycle=True,
        trajectory_complete=True,
    )
    healthy = _healthy_canary(rollback)
    common = {
        "capabilities": (capability,),
        "workload_kinds": ("terminal", "research"),
        "complete_pairs": 10,
        "quality_regression": False,
        "request_trace_match_rate": 1.0,
        "trajectory_complete_rate": 1.0,
        "rollback_config_hash": rollback.bundle_hash,
        "required_canary_policy_fingerprint": healthy.policy_fingerprint,
    }

    self_declared = assess_default_on_readiness(
        **common, canary_health_decision=healthy
    )
    assert self_declared.status is ReadinessStatus.NOT_READY
    assert "capability_matrix_incomplete" in self_declared.gate_failures
    assert (
        self_declared.canary_health_decision_fingerprint == healthy.decision_fingerprint
    )
    unbound = dict(common)
    unbound.pop("required_canary_policy_fingerprint")
    unbound_decision = assess_default_on_readiness(
        **unbound, canary_health_decision=healthy
    )
    assert unbound_decision.status is ReadinessStatus.NOT_READY
    assert "canary_policy_binding_missing" in unbound_decision.gate_failures
    missing = assess_default_on_readiness(**common)
    assert missing.status is ReadinessStatus.NOT_READY
    assert "canary_health_missing" in missing.gate_failures

    rolled_back = assess_canary_health(
        policy=CanaryHealthPolicy(
            policy_version="health-v1",
            minimum_shadow_calls=1,
            minimum_enforce_sessions=1,
            max_provider_error_rate_delta=0.02,
        ),
        evidence=replace(
            CanaryHealthEvidence(
                shadow_call_count=1,
                shadow_request_trace_match_count=1,
                shadow_provider_attribution_complete_count=1,
                enforce_session_count=1,
                enforce_provider_attempt_count=1,
                enforce_provider_error_count=0,
                baseline_provider_error_rate=0.0,
                security_violation_count=0,
                trajectory_incomplete_count=0,
                quality_regression=False,
                baseline_provider_attempt_count=1,
                baseline_provider_error_count=0,
                enforce_sessions_with_provider_attempt_count=1,
            ),
            security_violation_count=1,
        ),
        rollback_bundle=rollback,
    )
    decision = assess_default_on_readiness(**common, canary_health_decision=rolled_back)
    assert decision.status is ReadinessStatus.ROLLBACK_REQUIRED
    assert "canary_rollback_required" in decision.gate_failures

    with pytest.raises(ValueError, match="cannot be enforce"):
        RollbackBundle.build(
            previous_mode=ContextCompilerMode.ENFORCE,
            previous_config={"mode": "enforce"},
            provider_capability_hash=canonical_json_hash({"openai": True}),
        )
