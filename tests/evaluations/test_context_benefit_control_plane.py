from __future__ import annotations

import pytest

from aworld.core.context.compiler import (
    ContextCompilerMode,
    ReadinessStatus,
    RollbackBundle,
    RolloutCapability,
    RolloutCohortPolicy,
    assess_default_on_readiness,
    assign_rollout_mode,
    canonical_json_hash,
)
from aworld.evaluations.context_benefit import (
    ContextEvaluationManifest,
    ContextTrialEvidence,
    ContextVariant,
    TrialFidelity,
    build_paired_deltas,
    summarize_context_benefit,
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


def test_context_only_manifest_and_paired_benefit_are_deterministic():
    manifest = _manifest()
    trials = (
        _trial(manifest, "baseline", 0.0, 1000),
        _trial(manifest, "candidate", 1.0, 600),
    )
    deltas = build_paired_deltas(
        trials, baseline_variant="baseline", candidate_variant="candidate"
    )
    first = summarize_context_benefit(
        deltas, bootstrap_samples=200, seed=13
    )
    second = summarize_context_benefit(
        deltas, bootstrap_samples=200, seed=13
    )
    assert first == second
    assert first.mean_reward_delta == 1.0
    assert first.metric_means["input_tokens"] == -400.0

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
        workload_kinds=("terminal",),
        complete_pairs=10,
        quality_regression=False,
        request_trace_match_rate=1.0,
        trajectory_complete_rate=1.0,
        rollback_config_hash=rollback.bundle_hash,
    )
    assert not_ready.status is ReadinessStatus.NOT_READY
    assert "cross_workload_evidence_missing" in not_ready.gate_failures

    ready = assess_default_on_readiness(
        capabilities=(ready_capability,),
        workload_kinds=("terminal", "research"),
        complete_pairs=10,
        quality_regression=False,
        request_trace_match_rate=1.0,
        trajectory_complete_rate=1.0,
        rollback_config_hash=rollback.bundle_hash,
    )
    assert ready.status is ReadinessStatus.READY

    missing_entry_point = assess_default_on_readiness(
        capabilities=(ready_capability,),
        required_capabilities=(("openai", "agent"), ("openai", "cli")),
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
        workload_kinds=("terminal", "research"),
        complete_pairs=2,
        quality_regression=False,
        request_trace_match_rate=1.0,
        trajectory_complete_rate=1.0,
        rollback_config_hash=rollback.bundle_hash,
    )
    assert too_small.status is ReadinessStatus.NOT_READY
    assert "insufficient_paired_evidence" in too_small.gate_failures
