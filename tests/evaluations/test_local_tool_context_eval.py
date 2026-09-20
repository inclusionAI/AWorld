from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]


def _load_driver():
    path = ROOT / "examples" / "evaluations" / "context_tool_workload.py"
    spec = importlib.util.spec_from_file_location("context_tool_workload", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_reporter():
    path = ROOT / "examples" / "evaluations" / "context_benefit_report.py"
    spec = importlib.util.spec_from_file_location("context_benefit_report", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_non_terminal_case_keeps_expected_result_outside_agent_workspace(tmp_path):
    driver = _load_driver()
    case_dir = tmp_path / "case"
    workspace = case_dir / "workspace"
    workspace.mkdir(parents=True)
    (case_dir / "case.json").write_text(
        json.dumps({"case_id": "generic", "workload_kind": "tool_research"})
    )
    (case_dir / "instruction.md").write_text("Create result.json")
    (case_dir / "expected.json").write_text(
        json.dumps({"artifact": "result.json", "exact": {"answer": 7}})
    )
    (workspace / "source.txt").write_text("agent-visible")

    case = driver.load_case(case_dir)

    assert case["workload_kind"] == "tool_research"
    assert not (case["workspace"] / "expected.json").exists()


def test_non_terminal_verifier_scores_exact_host_only_artifact(tmp_path):
    driver = _load_driver()
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps({"artifact": "result.json", "exact": {"answer": 7}}))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.json").write_text(json.dumps({"answer": 7}))

    passed = driver.verify_case(workspace, expected)
    (workspace / "result.json").write_text(json.dumps({"answer": 8}))
    failed = driver.verify_case(workspace, expected)

    assert passed["reward"] == 1
    assert passed["errors"] == []
    assert failed["reward"] == 0
    assert failed["errors"] == ["artifact_exact_value_mismatch"]


def test_experiment_manifest_rejects_mixed_runtime_source_fingerprints(tmp_path):
    reporter = _load_reporter()
    experiment = tmp_path / "experiment"
    experiment.mkdir()
    payload = {
        "benchmark_adapter": "local-tool-research/v1",
        "cases": [
            {
                "case_id": "case",
                "workload_kind": "tool_research",
                "checksum": "sha256:" + "1" * 64,
                "verifier_id": "exact-json-v1",
            }
        ],
        "variants": [
            {"name": "baseline"},
            {"name": "candidate", "context_compiler": {"mode": "enforce"}},
        ],
        "repeat": 1,
        "seed": 7,
    }
    results = []
    for variant, fingerprint in (
        ("baseline", "sha256:" + "a" * 64),
        ("candidate", "sha256:" + "b" * 64),
    ):
        run = experiment / "runs" / "case" / variant / "repeat-01"
        run.mkdir(parents=True)
        (run / "run_manifest.json").write_text(
            json.dumps(
                {
                    "aworld_source": {
                        "commit": "abc",
                        "source_fingerprint": fingerprint,
                    },
                    "invariants": {},
                    "container": {},
                }
            )
        )
        results.append({"task": "case", "variant": variant, "repetition": 1})

    try:
        reporter.experiment_manifest(experiment, payload, results)
    except ValueError as exc:
        assert (
            str(exc)
            == "experiment mixes AWorld runtime source fingerprints across runs"
        )
    else:
        raise AssertionError("mixed runtime source fingerprints must fail closed")


def test_benefit_report_consumes_real_artifact_contract_and_stays_not_ready_for_smoke(
    tmp_path,
):
    reporter = _load_reporter()
    experiment = tmp_path / "experiment"
    variants = [
        {
            "name": "legacy",
            "agent_memory_config": {"tool_result_offload": False},
            "docker_output_policy": {"max_inline_output_bytes": 1024},
        },
        {
            "name": "candidate",
            "agent_memory_config": {"tool_result_offload": True},
            "context_compiler": {"mode": "enforce", "universal_final": True},
            "docker_output_policy": {"max_inline_output_bytes": 512},
        },
    ]
    (experiment / "experiment_manifest.json").parent.mkdir(parents=True)
    (experiment / "experiment_manifest.json").write_text(
        json.dumps(
            {
                "benchmark_adapter": "local-tool-research/v1",
                "cases": [
                    {
                        "case_id": "case",
                        "workload_kind": "tool_research",
                        "checksum": "sha256:" + "a" * 64,
                        "verifier_id": "exact-json-v1",
                    }
                ],
                "variants": variants,
                "repeat": 1,
                "seed": 7,
                "normalized_cost_policy": reporter.NormalizedCostPolicy().to_dict(),
            }
        )
    )
    results = []
    for name, reward, tokens in (("legacy", 0, 100), ("candidate", 1, 60)):
        run = experiment / "runs" / "case" / name / "repeat-01"
        run.mkdir(parents=True)
        provider_payload = {
            "model": "test",
            "messages": [{"role": "user", "content": "x"}],
        }
        provider_call = {
            "provider_invoked": True,
            "provider_attempt_status": "attempted",
            "status": "success",
            "request_trace_match": True,
            "usage_normalized": {
                "prompt_tokens": tokens,
                "completion_tokens": 1,
                "total_tokens": tokens + 1,
                "cache_hit_tokens": 0,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
            "usage_raw": {
                "prompt_tokens": tokens,
                "completion_tokens": 1,
                "total_tokens": tokens + 1,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
            "provider_request": {
                "request_id": f"{name}-request",
                "provider_name": "openai",
                "payload": provider_payload,
                "capture_stage": "provider_prepared",
                "fidelity": "provider_prepared",
                "content_hash": reporter.value_hash(provider_payload),
                "serialized_checksum": None,
            },
        }
        (run / "provider_calls.json").write_text(json.dumps([provider_call]))
        (run / "context_trace.json").write_text("[]")
        (run / "verifier.json").write_text(json.dumps({"reward": reward}))
        (run / "task_response.json").write_text(
            json.dumps(
                {
                    "trajectory_build_result": {
                        "status": "complete",
                        "fidelity": "complete",
                        "trajectory_checksum": "sha256:" + "b" * 64,
                    }
                }
            )
        )
        (run / "run_manifest.json").write_text(
            json.dumps(
                {
                    "aworld_source": {"commit": "abc"},
                    "invariants": {"model": "test", "temperature": 0},
                    "container": {"image_id": "image"},
                }
            )
        )
        results.append(
            {
                "task": "case",
                "variant": name,
                "repetition": 1,
                "agent_exit_code": 0,
                "reward": reward,
                "context_metrics": {
                    "capture_integrity_available": True,
                    "raw_trajectory_available": True,
                    "prompt_tokens": tokens,
                },
            }
        )
    (experiment / "results.json").write_text(json.dumps(results))

    report = reporter.aggregate(
        [experiment],
        baseline="legacy",
        candidate="candidate",
        bootstrap_samples=200,
        seed=11,
    )

    assert report["combined_benefit"]["mean_reward_delta"] == 1.0
    assert report["benefit_evidence"]["path"] == "quality"
    assert report["attributed_benefit_evidence"] == report["benefit_evidence"]
    assert report["combined_benefit"]["metric_means"]["prompt_tokens"] == -40.0
    assert report["combined_benefit"]["metric_means"]["normalized_cost"] == -40.0
    assert (
        report["combined_benefit"]["metric_means"]["normalized_cost_microunits"]
        == -40_000_000.0
    )
    assert report["normalized_cost_policy_ready"] is True
    tampered = json.loads(json.dumps(report["workloads"]))
    tampered[0]["trials"][0]["metrics"]["normalized_cost_receipt"][
        "total_microunits"
    ] += 1
    assert reporter.normalized_cost_evidence_ready(tampered) is False
    legacy_only = json.loads(json.dumps(report["workloads"]))
    legacy_only[0]["normalized_cost_policy"] = {"status": "unavailable"}
    for trial in legacy_only[0]["trials"]:
        trial["metrics"].pop("normalized_cost_receipt", None)
        trial["metrics"].pop("normalized_cost_microunits", None)
    assert reporter.normalized_cost_evidence_ready(legacy_only) is False
    assert report["default_on_readiness"]["status"] == "not_ready"
    assert (
        "insufficient_paired_evidence"
        in report["default_on_readiness"]["gate_failures"]
    )
    assert (
        "cross_workload_evidence_missing"
        in report["default_on_readiness"]["gate_failures"]
    )
    assert (
        "provider_attribution_pairing_incomplete"
        in report["default_on_readiness"]["gate_failures"]
    )
    assert (
        "capability_matrix_incomplete"
        in report["default_on_readiness"]["gate_failures"]
    )
    assert (
        "canary_receipt_evidence_incomplete"
        in report["default_on_readiness"]["gate_failures"]
    )
    assert "rollback_bundle_missing" in report["default_on_readiness"]["gate_failures"]
    assert report["rollout_capabilities"] == []
    assert report["rollback_bundle"] is None


def test_benefit_report_accepts_only_explicit_cost_metric_for_efficiency_path():
    reporter = _load_reporter()
    neutral_reward = SimpleNamespace(lower=0.0, upper=0.0)
    cost_reduction = SimpleNamespace(lower=-3.0, upper=-1.0)
    summary = SimpleNamespace(
        reward_interval=neutral_reward,
        metric_intervals={"cost_per_successful_task": cost_reduction},
    )

    evidence = reporter.benefit_evidence(summary)

    assert evidence["proven"] is True
    assert evidence["path"] == "efficiency"
    assert evidence["cost_metric"] == "cost_per_successful_task"


def test_benefit_report_accepts_provider_work_reduction_with_quality_non_regression():
    reporter = _load_reporter()
    summary = SimpleNamespace(
        reward_interval=SimpleNamespace(lower=0.0, upper=0.2),
        metric_intervals={
            "provider_call_count": SimpleNamespace(lower=-20.0, upper=-2.0)
        },
    )

    evidence = reporter.benefit_evidence(summary)

    assert evidence == {
        "proven": True,
        "path": "execution_efficiency",
        "reason": "quality_non_regression_and_provider_work_confidence_upper_bound_negative",
        "cost_metric": "provider_call_count",
    }


def test_benefit_report_accepts_exact_uncached_input_reduction():
    reporter = _load_reporter()
    summary = SimpleNamespace(
        reward_interval=SimpleNamespace(lower=0.0, upper=0.0),
        metric_intervals={
            "uncached_input_tokens_exact": SimpleNamespace(
                lower=-200.0, upper=-20.0
            )
        },
    )

    evidence = reporter.benefit_evidence(summary)

    assert evidence["proven"] is True
    assert evidence["path"] == "execution_efficiency"
    assert evidence["cost_metric"] == "uncached_input_tokens_exact"


def test_cache_ablation_evidence_revalidates_plan_and_generic_preflight():
    reporter = _load_reporter()
    variants = [
        {
            "name": "adaptive-cache-off",
            "context_cache": {
                "enabled": False,
                "allow_provider_native_cache": False,
            },
            "context_compiler": {"mode": "enforce"},
        },
        {
            "name": "adaptive-cache-on",
            "context_cache": {
                "enabled": True,
                "allow_provider_native_cache": True,
            },
            "context_compiler": {"mode": "enforce"},
        },
    ]
    before = reporter.ContextVariant.build(
        variants[0]["name"], reporter.variant_settings(variants[0])
    )
    after = reporter.ContextVariant.build(
        variants[1]["name"], reporter.variant_settings(variants[1])
    )
    contrast = reporter.ContextAblationContrast.build(
        baseline=before,
        candidate=after,
        component=reporter.ContextAblationComponent.CACHE,
    )
    plan = reporter.ContextAblationPlan.build(
        name="generic-cache",
        variants=(before, after),
        contrasts=(contrast,),
    )
    manifest = {
        "variants": variants,
        "ablation_plan": plan.to_dict(),
        "cache_usage_preflight": {
            "schema_version": "aworld.cache-conformance-preflight/v1",
            "status": "passed",
            "cache_capability_observed": True,
            "exact_usage_coverage": 1.0,
            "observation_count": 8,
            "validated_modes": ["nonstream", "stream"],
            "failure_codes": [],
            "process_exit_code": 0,
            "run_nonce_hash": "sha256:" + "a" * 64,
        },
    }

    evidence = reporter.cache_ablation_evidence(
        manifest,
        baseline="adaptive-cache-off",
        candidate="adaptive-cache-on",
    )

    assert evidence["status"] == "available"
    assert evidence["changed_paths"] == [
        "context_cache.allow_provider_native_cache",
        "context_cache.enabled",
    ]
    manifest["cache_usage_preflight"]["exact_usage_coverage"] = 0.875
    unavailable = reporter.cache_ablation_evidence(
        manifest,
        baseline="adaptive-cache-off",
        candidate="adaptive-cache-on",
    )
    assert unavailable == {
        "status": "unavailable",
        "reason_code": "cache_preflight_not_exact",
    }


def test_cache_lowering_distinguishes_native_control_from_safety_only_prefix():
    reporter = _load_reporter()

    def call(status: str, strategy: str) -> dict:
        return {
            "context_rollout": {
                "provider_lowering": {
                    "cache_lowering_status": status,
                    "cache_lowering_strategy": strategy,
                }
            }
        }

    disabled = reporter.cache_lowering_run_evidence(
        [call("disabled", "explicit_opt_out")], cache_enabled=False
    )
    explicit = reporter.cache_lowering_run_evidence(
        [call("applied", "prompt_cache_key")], cache_enabled=True
    )
    anthropic = reporter.cache_lowering_run_evidence(
        [call("applied", "anthropic_cache_control")], cache_enabled=True
    )
    automatic = reporter.cache_lowering_run_evidence(
        [call("preserved", "exact_prefix_no_hint")], cache_enabled=True
    )
    unverified = reporter.cache_lowering_run_evidence(
        [call("unsupported", "provider_capability_not_declared")],
        cache_enabled=True,
    )

    assert disabled["status"] == "available"
    assert explicit["status"] == "available"
    assert anthropic["status"] == "available"
    assert automatic["status"] == "safety_only"
    assert automatic["reason_code"] == "provider_native_cache_control_not_applied"
    assert unverified["status"] == "safety_only"
    assert (
        unverified["reason_code"]
        == "provider_native_cache_capability_unverified"
    )


def test_cache_lowering_fails_closed_for_missing_or_mixed_receipts():
    reporter = _load_reporter()
    assert reporter.cache_lowering_run_evidence([], cache_enabled=True) == {
        "status": "unavailable",
        "reason_code": "provider_calls_missing",
    }
    evidence = reporter.cache_lowering_run_evidence(
        [
            {
                "context_rollout": {
                    "provider_lowering": {
                        "cache_lowering_status": "applied",
                        "cache_lowering_strategy": "prompt_cache_key",
                    }
                }
            },
            {
                "context_rollout": {
                    "provider_lowering": {
                        "cache_lowering_status": "preserved",
                        "cache_lowering_strategy": "exact_prefix_no_hint",
                    }
                }
            },
        ],
        cache_enabled=True,
    )
    assert evidence["status"] == "unavailable"
    assert evidence["reason_code"] == "explicit_provider_cache_lowering_incomplete"


def test_benefit_report_reads_frozen_intervals_from_real_summary_contract():
    from aworld.evaluations.context_benefit import (
        PairedContextDelta,
        summarize_context_benefit,
    )

    reporter = _load_reporter()
    summary = summarize_context_benefit(
        (
            PairedContextDelta(
                case_id="case",
                repeat=1,
                baseline_variant="legacy",
                candidate_variant="candidate",
                reward_delta=0.0,
                metric_deltas={"cost_per_successful_task": -1.0},
            ),
        ),
        bootstrap_samples=100,
        seed=7,
    )

    evidence = reporter.benefit_evidence(summary)

    assert evidence["proven"] is True
    assert evidence["path"] == "efficiency"
    assert evidence["cost_metric"] == "cost_per_successful_task"


def test_benefit_report_reads_losslessly_compressed_json(tmp_path):
    import lzma

    reporter = _load_reporter()
    expected = {"status": "complete", "count": 3}
    path = tmp_path / "task_response.json"
    with lzma.open(path.with_name(path.name + ".xz"), "wt", encoding="utf-8") as stream:
        json.dump(expected, stream)

    assert reporter.read_json(path) == expected


def test_normalized_cost_efficiency_requires_a_revalidated_policy():
    reporter = _load_reporter()
    summary = SimpleNamespace(
        reward_interval=SimpleNamespace(lower=0.0, upper=0.0),
        metric_intervals={
            "normalized_cost_microunits": SimpleNamespace(lower=-10.0, upper=-1.0)
        },
    )

    unavailable = reporter.benefit_evidence(summary)
    available = reporter.benefit_evidence(summary, normalized_cost_policy_ready=True)

    assert unavailable["proven"] is False
    assert available["proven"] is True
    assert available["cost_metric"] == "normalized_cost_microunits"

    old_float_only = SimpleNamespace(
        reward_interval=SimpleNamespace(lower=0.0, upper=0.0),
        metric_intervals={"normalized_cost": SimpleNamespace(lower=-10.0, upper=-1.0)},
    )
    assert (
        reporter.benefit_evidence(old_float_only, normalized_cost_policy_ready=True)[
            "proven"
        ]
        is False
    )

    conservative = SimpleNamespace(
        reward_interval=SimpleNamespace(lower=0.0, upper=0.0),
        metric_intervals={
            "normalized_cost_conservative_delta_microunits": SimpleNamespace(
                lower=-20.0, upper=-2.0
            )
        },
    )
    conservative_evidence = reporter.benefit_evidence(
        conservative, normalized_cost_policy_ready=True
    )
    assert conservative_evidence["proven"] is True
    assert conservative_evidence["cost_metric"] == (
        "normalized_cost_conservative_delta_microunits"
    )


def test_normalized_cost_bounds_are_conservative_for_missing_cache_and_failed_attempt():
    reporter = _load_reporter()
    provider_request = {
        "request_id": "dynamic",
        "provider_name": "openai",
        "payload": {"messages": [{"role": "user", "content": "hello"}]},
    }
    successful = {
        "provider_invoked": True,
        "provider_attempt_status": "attempted",
        "status": "success",
        "provider_request": provider_request,
        "response": {"content": "ok"},
        "usage_normalized": {"prompt_tokens": 100, "completion_tokens": 10},
        "usage_raw": {"prompt_tokens": 100, "completion_tokens": 10},
    }
    failed = {
        "provider_invoked": True,
        "provider_attempt_status": "attempted",
        "status": "failed",
        "provider_request": provider_request,
        "response": None,
    }

    bounds, reason = reporter.authoritative_normalized_usage_bounds(
        [successful, failed]
    )

    assert reason is None
    assert bounds["lower"] == {
        "input_tokens": 100,
        "cache_read_tokens": 100,
        "output_tokens": 10,
    }
    assert bounds["upper"]["input_tokens"] > 100
    assert bounds["upper"]["cache_read_tokens"] == 0
    assert bounds["upper"]["output_tokens"] == 10
    assert bounds["cache_bounded_call_count"] == 1
    assert bounds["provider_attempt_bounded_call_count"] == 1


def test_quality_path_does_not_require_normalized_cost_evidence():
    reporter = _load_reporter()
    summary = SimpleNamespace(
        reward_interval=SimpleNamespace(lower=0.1, upper=0.2),
        metric_intervals={},
    )

    evidence = reporter.benefit_evidence(summary, normalized_cost_policy_ready=False)

    assert evidence["proven"] is True
    assert evidence["path"] == "quality"


def test_task_benchmark_manifest_preserves_workload_and_verifier_identity(tmp_path):
    reporter = _load_reporter()
    payload = {
        "benchmark_adapter": "skillsbench-official-1.1",
        "tasks": ["pdf-excel-diff"],
        "dataset_sha256": "sha256:" + "a" * 64,
        "variants": [{"name": "baseline"}, {"name": "candidate"}],
        "repeat": 3,
        "seed": 7,
        "verifier_mode": "python-functions",
    }

    manifest = reporter.experiment_manifest(tmp_path, payload, [])

    assert manifest.workload_kind == "skills_bench"
    assert manifest.independent_verifier_id == (
        "python-functions-immutable-task-snapshot-v1"
    )


def test_normalized_usage_fails_closed_on_missing_or_conflicting_truth():
    reporter = _load_reporter()
    complete = {
        "provider_invoked": True,
        "provider_attempt_status": "attempted",
        "status": "success",
        "usage_normalized": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
            "cache_hit_tokens": 3,
        },
        "usage_raw": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
            "prompt_tokens_details": {"cached_tokens": 3},
        },
    }
    usage, reason = reporter.authoritative_normalized_usage([complete])
    assert usage == {"input_tokens": 10, "cache_read_tokens": 3, "output_tokens": 2}
    assert reason is None

    missing = json.loads(json.dumps(complete))
    del missing["usage_raw"]["prompt_tokens_details"]
    assert reporter.authoritative_normalized_usage([missing])[0] is None

    conflicting = json.loads(json.dumps(complete))
    conflicting["usage_raw"]["prompt_tokens"] = 11
    assert reporter.authoritative_normalized_usage([conflicting]) == (
        None,
        "provider_usage_conflict",
    )

    cache_conflict = json.loads(json.dumps(complete))
    cache_conflict["usage_raw"]["prompt_tokens_details"]["cached_tokens"] = 4
    assert reporter.authoritative_normalized_usage([cache_conflict]) == (
        None,
        "provider_cache_usage_conflicting_views",
    )

    cache_exceeds_input = json.loads(json.dumps(complete))
    cache_exceeds_input["usage_normalized"]["cache_hit_tokens"] = 11
    cache_exceeds_input["usage_raw"]["prompt_tokens_details"]["cached_tokens"] = 11
    assert reporter.authoritative_normalized_usage([cache_exceeds_input]) == (
        None,
        "provider_cache_usage_exceeds_input",
    )


def test_provider_metrics_report_exact_cache_coverage_without_false_zero():
    reporter = _load_reporter()
    exact = {
        "usage_normalized": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "cache_hit_tokens": 3,
        },
        "usage_raw": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "prompt_tokens_details": {"cached_tokens": 3},
        },
    }
    missing = {
        "usage_normalized": {"prompt_tokens": 20, "completion_tokens": 4},
        "usage_raw": {"prompt_tokens": 20, "completion_tokens": 4},
    }

    metrics = reporter.authoritative_provider_metrics([exact, missing])

    assert metrics["cache_usage_exact_call_count"] == 1
    assert metrics["cache_usage_exact_coverage"] == 0.5
    assert metrics["cache_usage_bounded_call_count"] == 1
    assert metrics["cache_read_tokens"] == 3
    assert metrics["cache_usage_exact_input_tokens"] == 10
    assert metrics["uncached_input_tokens_exact"] == 7


def test_trial_drops_provisional_uncached_tokens_when_provider_coverage_is_partial(
    tmp_path, monkeypatch
):
    reporter = _load_reporter()
    experiment = tmp_path / "experiment"
    run_dir = experiment / "runs" / "case" / "candidate" / "repeat-00"
    run_dir.mkdir(parents=True)
    (run_dir / "provider_calls.json").write_text("[{}]", encoding="utf-8")
    monkeypatch.setattr(
        reporter,
        "authoritative_provider_metrics",
        lambda calls: {
            "cache_usage_exact_coverage": 0.5,
            "uncached_input_tokens_exact": 7,
        },
    )
    manifest = reporter.ContextEvaluationManifest.build(
        experiment_id="cache-evidence",
        workload_id="tool",
        workload_kind="tool_research",
        dataset_checksum="sha256:" + "1" * 64,
        repository_snapshot="commit:abc",
        environment_hash="sha256:" + "2" * 64,
        inference_profile_hash="sha256:" + "3" * 64,
        variants=(
            reporter.ContextVariant.build("baseline", {}),
            reporter.ContextVariant.build("candidate", {}),
        ),
        case_ids=("case",),
        repeats=1,
        interleaving_seed=1,
        independent_verifier_id="exact-json-v1",
    )

    trial, _ = reporter.trial_from_result(
        experiment,
        manifest,
        {
            "task": "case",
            "variant": "candidate",
            "repetition": 0,
            "reward": 1,
            "agent_exit_code": 0,
            "context_metrics": {"uncached_input_tokens_exact": 999},
        },
    )

    assert trial is not None
    assert "uncached_input_tokens_exact" not in trial.metrics
    assert trial.metrics["cache_usage_exact_coverage"] == 0.5


def test_captured_cache_receipt_mismatch_fails_closed():
    reporter = _load_reporter()
    call = {
        "provider_invoked": True,
        "provider_attempt_status": "attempted",
        "status": "success",
        "usage_normalized": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "cache_hit_tokens": 3,
        },
        "usage_raw": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "prompt_tokens_details": {"cached_tokens": 3},
        },
        "cache_usage_receipt": {
            "schema_version": "aworld.cache-usage-receipt.v1",
            "fidelity": "exact",
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_read_tokens": 9,
        },
    }

    receipt = reporter.authoritative_cache_usage_receipt(call)

    assert receipt["fidelity"] == "conflicting"
    assert receipt["reason_code"] == "captured_cache_usage_receipt_mismatch"
    assert reporter.authoritative_normalized_usage([call]) == (
        None,
        "provider_cache_usage_receipt_conflict",
    )


def _turn_receipt(reporter, kind, cause, identity, parent=None):
    return {
        "schema_version": "aworld.context.turn-economics.v1",
        "task_epoch": 0,
        "turn_kind": kind,
        "cause": cause,
        "cause_supported": True,
        "turn_id_hash": reporter.value_hash({f"{kind}_turn": identity}),
        "request_id_hash": reporter.value_hash({"request_id": identity})
        if kind == "model"
        else None,
        "tool_call_id_hash": reporter.value_hash({"tool_call_id": identity})
        if kind == "tool"
        else None,
        "parent_turn_id_hash": parent,
        "evidence_hash": None,
    }


def _retrieval_content(*, artifact_content_hash="sha256:" + "1" * 64):
    chunk = "x" * 256
    return {
        "type": "text",
        "content": chunk,
        "artifact_ref": "opaque-ref",
        "offset": 4096,
        "next_offset": 4352,
        "returned_bytes": 256,
        "total_bytes": 131072,
        "content_sha256": artifact_content_hash,
        "chunk_sha256": "sha256:" + hashlib.sha256(chunk.encode()).hexdigest(),
        "complete": False,
    }


def _retrieval_receipt(
    reporter, *, consumed, artifact_content_hash="sha256:" + "1" * 64
):
    content = _retrieval_content(artifact_content_hash=artifact_content_hash)
    result_hash = reporter.value_hash(content)
    plan = {
        "schema_version": "aworld.context.artifact-retrieval-plan.v1",
        "owner_code": reporter.value_hash({"owner_tool": "generic_stream"}),
        "action_code": reporter.value_hash(
            {"retrieval_action": "read_output_artifact"}
        ),
        "artifact_ref_hash": reporter.value_hash({"artifact_ref": "opaque-ref"}),
        "artifact_content_hash": artifact_content_hash,
        "artifact_byte_count": 131072,
        "offset": 4096,
        "limit": 256,
        "consumer_tool_call_id_hash": reporter.value_hash({"tool_call_id": "retrieve"}),
    }
    value = {
        **plan,
        "schema_version": "aworld.context.artifact-retrieval-receipt.v1",
        "plan_fingerprint": reporter.value_hash(plan),
        "returned_offset": 4096,
        "next_offset": 4352,
        "returned_byte_count": 256,
        "chunk_checksum": content["chunk_sha256"],
        "source_content_hash": artifact_content_hash,
        "result_content_hash": result_hash,
        "complete": False,
        "next_request_id_hash": reporter.value_hash({"request_id": "after"})
        if consumed
        else None,
        "consumed_content_hash": result_hash if consumed else None,
        "consumed": consumed,
    }
    return value


def test_turn_artifact_economics_uses_only_typed_truth(tmp_path):
    reporter = _load_reporter()
    artifact_bytes = b"x" * 131072
    artifact_content_hash = "sha256:" + hashlib.sha256(artifact_bytes).hexdigest()
    tool_retrieval = _retrieval_receipt(
        reporter, consumed=False, artifact_content_hash=artifact_content_hash
    )
    provider_consumption = _retrieval_receipt(
        reporter, consumed=True, artifact_content_hash=artifact_content_hash
    )
    raw = [
        {
            "state": {
                "input": {
                    "action_result": [
                        {
                            "tool_call_id": "retrieve",
                            "tool_name": "generic_stream",
                            "action_name": "read_output_artifact",
                            "content": _retrieval_content(
                                artifact_content_hash=artifact_content_hash
                            ),
                            "metadata": {
                                "turn_economics": _turn_receipt(
                                    reporter,
                                    "tool",
                                    "artifact_retrieval",
                                    "retrieve",
                                    parent=reporter.value_hash({"model_turn": "after"}),
                                ),
                                "tool_output_policy": {
                                    "policy_version": "v1",
                                    "raw_byte_count": 131072,
                                    "raw_checksum": "sha256:"
                                    + hashlib.sha256(artifact_bytes).hexdigest(),
                                    "inline_tokens": 128,
                                    "offloaded_tokens": 32640,
                                    "artifact_ref": "opaque",
                                    "context_artifact_ref": "opaque-context",
                                    "context_artifact_role": "audit_snapshot",
                                    "upstream_artifacts": [
                                        {
                                            "ref": "opaque",
                                            "content_hash": artifact_content_hash,
                                            "byte_count": 131072,
                                            "owner_tool": "generic_stream",
                                            "retrieval_action": "read_output_artifact",
                                        }
                                    ],
                                },
                                "artifact_retrieval": tool_retrieval,
                            },
                        }
                    ]
                }
            }
        }
    ]
    calls = [
        {
            "request_id": "after",
            "turn_economics": _turn_receipt(
                reporter,
                "model",
                "artifact_retrieval",
                "after",
                parent=reporter.value_hash({"tool_turn": "retrieve"}),
            ),
            "artifact_retrieval_consumption": [provider_consumption],
        }
    ]
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(artifact_bytes)

    summary = reporter.turn_artifact_economics_summary(calls, raw, [artifact])

    assert summary["turn_causes"]["status"] == "available"
    assert summary["turn_causes"]["counts"]["artifact_retrieval"] == {
        "model": 1,
        "tool": 1,
    }
    assert summary["tool_outputs"] == {
        "status": "available",
        "raw_bytes": 131072,
        "inline_tokens": 128,
        "offloaded_tokens": 32640,
        "double_offload_count": 0,
        "audit_snapshot_count": 1,
    }
    assert summary["retrieval"]["retrieved_bytes"] == 256
    assert summary["retrieval"]["consumed_count"] == 1
    assert summary["retrieval"]["opportunity_count"] == 1
    assert summary["retrieval"]["consumption_coverage"] == 1.0
    assert summary["artifacts"] == {"persisted_count": 1, "persisted_bytes": 131072}
    replayed_raw = json.loads(json.dumps(raw))
    replayed_raw[0]["state"]["input"]["action_result"].append(
        json.loads(json.dumps(raw[0]["state"]["input"]["action_result"][0]))
    )
    replayed = reporter.turn_artifact_economics_summary(calls, replayed_raw, [artifact])
    assert replayed["turn_causes"]["status"] == "unavailable"
    assert replayed["retrieval"]["status"] == "unavailable"

    broken_parent_calls = json.loads(json.dumps(calls))
    broken_parent_calls[0]["turn_economics"]["parent_turn_id_hash"] = (
        "sha256:" + "f" * 64
    )
    broken_parent = reporter.turn_artifact_economics_summary(
        broken_parent_calls, raw, [artifact]
    )
    assert broken_parent["turn_causes"]["status"] == "unavailable"
    runs = [
        {
            "experiment": "generic",
            "case_id": "noisy",
            "repeat": 1,
            "variant": variant,
            "summary": summary,
        }
        for variant in ("legacy", "candidate")
    ]
    delta = reporter.paired_turn_artifact_deltas(
        runs, baseline="legacy", candidate="candidate"
    )[0]
    assert delta["status"] == "available"
    assert delta["candidate_minus_baseline"]["retrieved_bytes"] == 0


def test_turn_artifact_economics_missing_receipts_is_unavailable_not_heuristic():
    reporter = _load_reporter()
    calls = [
        {
            "request": {
                "messages": [{"content": "read_output_artifact retry validation"}]
            }
        }
    ]
    raw = [{"action_result": [{"content": "artifact retrieved", "metadata": {}}]}]

    summary = reporter.turn_artifact_economics_summary(calls, raw, [])

    assert summary["turn_causes"]["status"] == "unavailable"
    assert summary["turn_causes"]["counts"] == {}
    assert summary["tool_outputs"]["status"] == "unavailable"
    assert summary["retrieval"]["status"] == "not_applicable"
    assert summary["retrieval"]["opportunity_count"] == 0


def test_turn_economics_recovers_only_cryptographically_bound_framework_retry():
    reporter = _load_reporter()
    first = _turn_receipt(reporter, "model", "initial_input", "request-1")
    retry = _turn_receipt(reporter, "model", "unavailable", "request-2")
    retry["cause_supported"] = False
    tool = _turn_receipt(
        reporter,
        "tool",
        "model_choice",
        "tool-1",
        parent=retry["turn_id_hash"],
    )
    calls = [
        {
            "request_id": "request-1",
            "call_id": "logical-call",
            "step_id": "step-1",
            "task_id": "task-1",
            "model": "model-1",
            "provider_name": "openai",
            "attempt": 1,
            "status": "failed",
            "provider_invoked": True,
            "turn_economics": first,
        },
        {
            "request_id": "request-2",
            "call_id": "logical-call",
            "step_id": "step-1",
            "task_id": "task-1",
            "model": "model-1",
            "provider_name": "openai",
            "attempt": 2,
            "status": "success",
            "provider_invoked": True,
            "turn_economics": retry,
        },
    ]
    content = "ok"
    raw = [
        {
            "action_result": [
                {
                    "tool_call_id": "tool-1",
                    "content": content,
                    "metadata": {
                        "turn_economics": tool,
                        "tool_output_policy": {
                            "raw_byte_count": len(content),
                            "raw_checksum": "sha256:"
                            + hashlib.sha256(content.encode()).hexdigest(),
                            "inline_tokens": 1,
                            "offloaded_tokens": 0,
                            "artifact_ref": None,
                            "context_artifact_ref": None,
                            "context_artifact_role": None,
                            "upstream_artifacts": [],
                        },
                    },
                }
            ]
        }
    ]

    summary = reporter.turn_artifact_economics_summary(calls, raw, [])

    assert summary["turn_causes"]["status"] == "available"
    assert summary["turn_causes"]["inferred_framework_retry_count"] == 1
    assert summary["turn_causes"]["counts"]["framework_retry"]["model"] == 1

    unbound = json.loads(json.dumps(calls))
    unbound[1]["step_id"] = "different-step"
    assert (
        reporter.turn_artifact_economics_summary(unbound, raw, [])["turn_causes"][
            "status"
        ]
        == "unavailable"
    )


def test_report_revalidates_manifest_bound_context_artifact_files(tmp_path):
    reporter = _load_reporter()
    run = tmp_path / "run"
    artifact = run / "tool-output-artifacts" / "context.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact")
    content_hash = "sha256:" + hashlib.sha256(b"artifact").hexdigest()
    manifest = {
        "capture": {
            "context_tool_output_artifacts": [
                {
                    "artifact_ref_hash": "sha256:" + "1" * 64,
                    "content_hash": content_hash,
                    "byte_count": 8,
                    "path": "tool-output-artifacts/context.bin",
                }
            ]
        }
    }
    (run / "run_manifest.json").write_text(json.dumps(manifest))

    assert reporter.validated_context_artifact_files(run) == [artifact.resolve()]
    artifact.write_bytes(b"tampered")
    assert reporter.validated_context_artifact_files(run) == []


def test_report_revalidates_manifest_bound_upstream_artifact_files(tmp_path):
    reporter = _load_reporter()
    run = tmp_path / "run"
    context_artifact = run / "tool-output-artifacts" / "context.bin"
    upstream_artifact = run / "tool-output-artifacts" / "upstream.bin"
    context_artifact.parent.mkdir(parents=True)
    context_artifact.write_bytes(b"context")
    upstream_artifact.write_bytes(b"upstream")

    def entry(path):
        data = path.read_bytes()
        return {
            "artifact_ref_hash": "sha256:" + "1" * 64,
            "content_hash": "sha256:" + hashlib.sha256(data).hexdigest(),
            "byte_count": len(data),
            "path": str(path.relative_to(run)),
        }

    manifest = {
        "capture": {
            "context_tool_output_artifacts": [entry(context_artifact)],
            "upstream_tool_output_artifacts": [entry(upstream_artifact)],
        }
    }
    (run / "run_manifest.json").write_text(json.dumps(manifest))

    assert reporter.validated_context_artifact_files(run) == sorted(
        [context_artifact.resolve(), upstream_artifact.resolve()]
    )


def _compiler_plan_evidence(
    reporter,
    *,
    request_id,
    candidate_hash,
    receipt_entries,
    messages_count,
    tools_shape="null",
    tools_count=None,
    subject="candidate_selected",
):
    entries = [
        {key: value for key, value in entry.items() if key != "canonical_value_bytes"}
        for entry in receipt_entries
    ]
    projection = {
        "schema_version": "aworld.context.attribution-plan-fingerprint.v2",
        "request_id_hash": reporter.value_hash({"request_id": request_id}),
        "candidate_content_hash": candidate_hash,
        "subject": subject,
        "messages_shape": "array",
        "messages_count": messages_count,
        "tools_shape": tools_shape,
        "tools_count": tools_count,
        "entries": entries,
    }
    return {
        **projection,
        "schema_version": "aworld.context.attribution-plan.v2",
        "plan_fingerprint": reporter.value_hash(projection),
        "entry_count": len(entries),
    }


def test_benefit_report_aggregates_receipts_and_never_classifies_missing_prompt():
    reporter = _load_reporter()
    payload = {"messages": [{"role": "user", "content": "actual"}], "model": "gpt"}
    message_bytes = len(reporter.canonical_json_bytes(payload["messages"][0]))
    total_bytes = len(reporter.canonical_json_bytes(payload))
    candidate_hash = "sha256:" + "a" * 64
    receipt = {
        "schema_version": "aworld.context.provider-attribution.v2",
        "subject": "candidate_selected",
        "status": "available",
        "serialization": "provider_prepared_canonical_json",
        "provider_request_content_hash": reporter.value_hash(payload),
        "canonical_request_checksum": reporter.value_hash(payload),
        "plan_request_id_hash": reporter.value_hash({"request_id": "r1"}),
        "candidate_content_hash": candidate_hash,
        "messages_shape": "array",
        "messages_count": 1,
        "tools_shape": "null",
        "tools_count": None,
        "provider_tools_shape": "absent",
        "tools_lowering": "null_to_absent",
        "total_canonical_bytes": total_bytes,
        "attributed_value_bytes": message_bytes,
        "provider_envelope_and_params": total_bytes - message_bytes,
        "byte_conservation": True,
        "entry_count": 1,
        "entries": [
            {
                "item_identity_hash": "sha256:" + "b" * 64,
                "owner_code": "model_final_messages",
                "kind": "user",
                "source_kind": "agent",
                "stability": "turn_dynamic",
                "collection": "messages",
                "ordinal": 0,
                "content_hash": reporter.value_hash(payload["messages"][0]),
                "token_estimate": {"value": 1, "estimator": "test-v1", "exact": False},
                "residency": "dynamic",
                "canonical_value_bytes": message_bytes,
            }
        ],
    }
    compiler_plan = _compiler_plan_evidence(
        reporter,
        request_id="r1",
        candidate_hash=candidate_hash,
        receipt_entries=receipt["entries"],
        messages_count=1,
    )
    receipt["plan_fingerprint"] = compiler_plan["plan_fingerprint"]
    calls = [
        {
            "request_id": "r1",
            "provider_invoked": True,
            "provider_attempt_status": "attempted",
            "status": "success",
            "provider_request": {
                "request_id": "r1",
                "payload": payload,
                "content_hash": reporter.value_hash(payload),
            },
            "context_rollout": {
                "candidate_snapshot": {
                    "content_hash": candidate_hash,
                    "attribution_plan_fingerprint": compiler_plan["plan_fingerprint"],
                },
                "compiler_attribution_plan": compiler_plan,
                "provider_lowering": {
                    "candidate_content_hash": candidate_hash,
                    "attribution": receipt,
                },
                # Older trajectory projection used a global seen-set and
                # stringified this shared sibling. The independently retained
                # provider-lowering receipt remains authoritative.
                "provider_attribution": {
                    "subject": "candidate_selected",
                    "subject_content_hash": candidate_hash,
                    "plan_fingerprint": compiler_plan["plan_fingerprint"],
                    "attribution": str(receipt),
                },
            },
        },
        {
            "request": {
                "messages": [{"role": "system", "content": "must-not-be-classified"}]
            }
        },
    ]

    summary = reporter.provider_attribution_summary(calls)

    assert summary["status"] == "unavailable"
    assert summary["coverage_rate"] == 0.5
    assert summary["byte_conservation"] is False
    assert summary["by_dimension"]["owner"] == {"model_final_messages": message_bytes}
    assert summary["unavailable_receipt_count"] == 1
    assert summary["fallback"] == "none"
    assert "must-not-be-classified" not in repr(summary)

    # A transport failure after provider invocation has no trusted usage, but
    # it still has an immutable provider request and a valid attribution
    # receipt. Request attribution and billing evidence are separate gates.
    failed_attempt = json.loads(json.dumps(calls[0]))
    failed_attempt["status"] = "failed"
    failed_summary = reporter.provider_attribution_summary([failed_attempt])
    assert failed_summary["status"] == "available"
    assert failed_summary["coverage_rate"] == 1.0
    assert failed_summary["byte_conservation"] is True


def test_benefit_report_marks_all_missing_attribution_unavailable():
    reporter = _load_reporter()

    summary = reporter.provider_attribution_summary(
        [{"request": {"messages": [{"role": "user", "content": "secret"}]}}]
    )

    assert summary["status"] == "unavailable"
    assert summary["available_receipt_count"] == 0
    assert summary["reason"] == "provider_attribution_incomplete"
    assert summary["by_dimension"]["owner"] == {}


def test_task_experiment_manifest_preserves_benchmark_workload_identity(tmp_path):
    reporter = _load_reporter()
    experiment = tmp_path / "skills-eval"
    experiment.mkdir()
    manifest = reporter.experiment_manifest(
        experiment,
        {
            "benchmark_adapter": "skillsbench-official-1.1",
            "tasks": ["task"],
            "variants": [{"name": "baseline"}, {"name": "candidate"}],
            "repeat": 3,
            "seed": 7,
            "verifier_mode": "python-functions",
        },
        [],
    )

    assert manifest.workload_kind == "skills_bench"
    assert (
        manifest.independent_verifier_id
        == "python-functions-immutable-task-snapshot-v1"
    )


def test_benefit_report_rejects_legal_owner_tamper_against_compiler_plan():
    reporter = _load_reporter()
    payload = {"messages": [{"role": "user", "content": "actual"}], "model": "gpt"}
    forged = {
        "schema_version": "aworld.context.provider-attribution.v2",
        "subject": "candidate_selected",
        "status": "available",
        "serialization": "provider_prepared_canonical_json",
        "provider_request_content_hash": reporter.value_hash(payload),
        "canonical_request_checksum": reporter.value_hash(payload),
        "plan_request_id_hash": reporter.value_hash({"request_id": "r1"}),
        "candidate_content_hash": "sha256:" + "a" * 64,
        "messages_count": 1,
        "tools_shape": "null",
        "tools_count": None,
        "provider_tools_shape": "absent",
        "tools_lowering": "null_to_absent",
        "total_canonical_bytes": len(reporter.canonical_json_bytes(payload)),
        "attributed_value_bytes": 1,
        "provider_envelope_and_params": len(reporter.canonical_json_bytes(payload)) - 1,
        "byte_conservation": True,
        "entry_count": 1,
        "entries": [
            {
                "item_identity_hash": "sha256:" + "b" * 64,
                "owner_code": "progressive_skill",
                "kind": "user",
                "source_kind": "agent",
                "stability": "turn_dynamic",
                "collection": "messages",
                "ordinal": 0,
                "content_hash": reporter.value_hash(payload["messages"][0]),
                "token_estimate": {"value": 1, "estimator": "test-v1", "exact": False},
                "residency": "dynamic",
                "canonical_value_bytes": 1,
            }
        ],
    }
    compiler_entry = dict(forged["entries"][0])
    compiler_entry["owner_code"] = "model_final_messages"
    compiler_plan = _compiler_plan_evidence(
        reporter,
        request_id="r1",
        candidate_hash="sha256:" + "a" * 64,
        receipt_entries=[compiler_entry],
        messages_count=1,
    )
    forged["plan_fingerprint"] = compiler_plan["plan_fingerprint"]
    calls = [
        {
            "request_id": "r1",
            "provider_request": {
                "request_id": "r1",
                "payload": payload,
                "content_hash": reporter.value_hash(payload),
                "capture_stage": "provider_prepared",
                "fidelity": "provider_prepared",
            },
            "context_rollout": {
                "candidate_snapshot": {
                    "content_hash": "sha256:" + "a" * 64,
                    "attribution_plan_fingerprint": compiler_plan["plan_fingerprint"],
                },
                "compiler_attribution_plan": compiler_plan,
                "provider_lowering": {
                    "candidate_content_hash": "sha256:" + "a" * 64,
                    "attribution": forged,
                },
            },
        }
    ]

    summary = reporter.provider_attribution_summary(calls)

    assert summary["status"] == "unavailable"
    assert summary["invalid_receipt_count"] == 1
    assert summary["byte_conservation"] is False
    assert "progressive_skill" not in repr(summary)


def test_provider_attribution_deltas_are_run_bound_and_unsupported_without_baseline():
    reporter = _load_reporter()
    available = {
        "status": "available",
        "by_dimension": {
            "owner": {"model_final_messages": 10},
            "kind": {"user": 10},
            "source_kind": {"agent": 10},
            "residency": {"dynamic": 10},
        },
    }
    legacy_available = {**available, "subject": "legacy_observed"}
    candidate_available = {**available, "subject": "candidate_selected"}
    rows = [
        {
            "experiment": "exp-a",
            "run": "legacy-run",
            "case_id": "case",
            "repeat": 0,
            "variant": "legacy",
            "summary": legacy_available,
        },
        {
            "experiment": "exp-a",
            "run": "candidate-run",
            "case_id": "case",
            "repeat": 0,
            "variant": "candidate",
            "summary": candidate_available,
        },
        {
            "experiment": "exp-b",
            "run": "candidate-only",
            "case_id": "case",
            "repeat": 0,
            "variant": "candidate",
            "summary": candidate_available,
        },
    ]

    deltas = reporter.paired_attribution_deltas(
        rows, baseline="legacy", candidate="candidate"
    )

    assert deltas[0]["status"] == "available"
    assert deltas[0]["baseline_run"] == "legacy-run"
    assert deltas[0]["candidate_run"] == "candidate-run"
    assert deltas[1]["status"] == "unsupported"
    assert deltas[1]["reason"] == "paired_variant_missing"
    assert deltas[1]["baseline_run"] is None


def test_provider_attribution_delta_rejects_subject_mismatch():
    reporter = _load_reporter()
    dimensions = {
        "owner": {},
        "kind": {},
        "source_kind": {},
        "residency": {},
    }
    wrong = {
        "status": "available",
        "subject": "candidate_selected",
        "by_dimension": dimensions,
    }
    rows = [
        {
            "experiment": "exp",
            "run": "legacy",
            "case_id": "case",
            "repeat": 1,
            "variant": "legacy",
            "summary": wrong,
        },
        {
            "experiment": "exp",
            "run": "candidate",
            "case_id": "case",
            "repeat": 1,
            "variant": "candidate",
            "summary": wrong,
        },
    ]

    delta = reporter.paired_attribution_deltas(
        rows, baseline="legacy", candidate="candidate"
    )[0]

    assert delta["status"] == "unsupported"
    assert delta["reason"] == "paired_attribution_subject_mismatch"


def test_provider_attribution_delta_supports_candidate_to_candidate_ablation():
    reporter = _load_reporter()
    common = {
        "status": "available",
        "subject": "candidate_selected",
        "total_canonical_bytes": 100,
        "per_call": [],
        "by_dimension": {
            "owner": {},
            "kind": {},
            "source_kind": {},
            "residency": {},
        },
        "dimension_resolution": {
            "owner": "compiler_owner_v1",
            "kind": "provider_occurrence_kind_v1",
            "source_kind": "provider_occurrence_source_v1",
            "residency": "compiler_logical_residency_v1",
        },
    }
    rows = [
        {
            "experiment": "exp",
            "run": "before",
            "case_id": "case",
            "repeat": 1,
            "variant": "before",
            "summary": common,
        },
        {
            "experiment": "exp",
            "run": "after",
            "case_id": "case",
            "repeat": 1,
            "variant": "after",
            "summary": {**common, "total_canonical_bytes": 90},
        },
    ]

    delta = reporter.paired_attribution_deltas(
        rows,
        baseline="before",
        candidate="after",
        allow_candidate_baseline=True,
    )[0]

    assert delta["status"] == "available"
    assert delta["total_canonical_bytes_delta"] == -10


def test_provider_attribution_delta_marks_owner_and_residency_resolution_mismatch():
    reporter = _load_reporter()
    common = {
        "status": "available",
        "total_canonical_bytes": 100,
        "by_dimension": {
            "owner": {"unknown": 40},
            "kind": {"user": 40},
            "source_kind": {"agent": 40},
            "residency": {"unknown": 40},
        },
    }
    legacy = {
        **common,
        "subject": "legacy_observed",
        "dimension_resolution": {
            "owner": "legacy_model_boundary_owner_v1",
            "kind": "provider_occurrence_kind_v1",
            "source_kind": "provider_occurrence_source_v1",
            "residency": "legacy_unknown_residency_v1",
        },
    }
    candidate = {
        **common,
        "subject": "candidate_selected",
        "total_canonical_bytes": 90,
        "dimension_resolution": {
            "owner": "compiler_owner_v1",
            "kind": "provider_occurrence_kind_v1",
            "source_kind": "provider_occurrence_source_v1",
            "residency": "compiler_logical_residency_v1",
        },
    }
    delta = reporter.paired_attribution_deltas(
        [
            {
                "experiment": "exp",
                "run": "legacy",
                "case_id": "case",
                "repeat": 1,
                "variant": "legacy",
                "summary": legacy,
            },
            {
                "experiment": "exp",
                "run": "candidate",
                "case_id": "case",
                "repeat": 1,
                "variant": "candidate",
                "summary": candidate,
            },
        ],
        baseline="legacy",
        candidate="candidate",
    )[0]

    assert delta["status"] == "available"
    assert delta["total_canonical_bytes_delta"] == -10
    assert delta["by_dimension_delta"]["owner"] is None
    assert delta["by_dimension_delta"]["residency"] is None
    assert delta["dimension_status"]["owner"]["reason"] == "resolution_mismatch"
    assert delta["dimension_status"]["kind"]["status"] == "available"


def test_request_amplification_separates_aligned_and_extra_call_bytes():
    reporter = _load_reporter()
    baseline = {
        "total_canonical_bytes": 250,
        "per_call": [
            {
                "ordinal": 0,
                "total_canonical_bytes": 100,
                "message_bytes": 60,
                "tool_schema_bytes": 30,
                "provider_envelope_and_params": 10,
                "messages_content_hash": "sha256:" + "a" * 64,
                "tools_content_hash": "sha256:" + "b" * 64,
            },
            {
                "ordinal": 1,
                "total_canonical_bytes": 150,
                "message_bytes": 110,
                "tool_schema_bytes": 30,
                "provider_envelope_and_params": 10,
                "messages_content_hash": "sha256:" + "c" * 64,
                "tools_content_hash": "sha256:" + "b" * 64,
            },
        ],
    }
    candidate = {
        "total_canonical_bytes": 440,
        "per_call": [
            {
                "ordinal": 0,
                "total_canonical_bytes": 110,
                "message_bytes": 60,
                "tool_schema_bytes": 30,
                "provider_envelope_and_params": 20,
                "messages_content_hash": "sha256:" + "a" * 64,
                "tools_content_hash": "sha256:" + "b" * 64,
            },
            {
                "ordinal": 1,
                "total_canonical_bytes": 160,
                "message_bytes": 120,
                "tool_schema_bytes": 30,
                "provider_envelope_and_params": 10,
                "messages_content_hash": "sha256:" + "d" * 64,
                "tools_content_hash": "sha256:" + "b" * 64,
            },
            {
                "ordinal": 2,
                "total_canonical_bytes": 170,
                "message_bytes": 130,
                "tool_schema_bytes": 30,
                "provider_envelope_and_params": 10,
                "messages_content_hash": "sha256:" + "e" * 64,
                "tools_content_hash": "sha256:" + "b" * 64,
            },
        ],
    }

    delta = reporter.request_amplification_delta(baseline, candidate)

    assert delta == {
        "status": "available",
        "baseline_call_count": 2,
        "candidate_call_count": 3,
        "aligned_call_count": 2,
        "candidate_only_call_count": 1,
        "baseline_only_call_count": 0,
        "aligned_provider_bytes_delta": 20,
        "candidate_only_provider_bytes": 170,
        "baseline_only_provider_bytes": 0,
        "total_provider_bytes_delta": 190,
        "byte_reconciliation": True,
        "first_call": {
            "messages_match": True,
            "tools_match": True,
            "model_visible_inputs_match": True,
            "provider_bytes_delta": 10,
            "message_bytes_delta": 0,
            "tool_schema_bytes_delta": 0,
            "provider_envelope_and_params_delta": 10,
        },
    }


def test_artifact_progress_is_recomputed_from_raw_trajectory_receipts():
    reporter = _load_reporter()
    raw = [
        {
            "state": {
                "input": {
                    "action_result": [
                        {
                            "metadata": {
                                "context_management": {
                                    "schema_version": "aworld.sandbox-artifact-progress/v1",
                                    "artifact_changed": False,
                                    "artifact_fingerprint_after": "same",
                                    "rollback_performed": False,
                                    "implicit_artifact_loss_detected": False,
                                }
                            }
                        }
                    ]
                }
            }
        },
        {
            "state": {
                "input": {
                    "action_result": [
                        {
                            "metadata": {
                                "context_management": {
                                    "schema_version": "aworld.sandbox-artifact-progress/v1",
                                    "artifact_changed": False,
                                    "artifact_fingerprint_after": "same",
                                    "rollback_performed": True,
                                    "implicit_artifact_loss_detected": True,
                                }
                            }
                        },
                        {
                            "metadata": {
                                "context_management": {
                                    "schema_version": "aworld.sandbox-artifact-progress/v1",
                                    "artifact_changed": True,
                                    "artifact_fingerprint_after": "new",
                                    "rollback_performed": False,
                                    "implicit_artifact_loss_detected": False,
                                }
                            }
                        },
                    ]
                }
            }
        },
    ]

    progress = reporter.artifact_progress_summary(raw)

    assert progress == {
        "status": "available",
        "evidence_basis": "raw_trajectory_sandbox_receipts",
        "artifact_receipt_count": 3,
        "artifact_change_count": 1,
        "new_artifact_state_count": 1,
        "rollback_count": 1,
        "implicit_artifact_loss_count": 1,
        "implicit_artifact_loss_prevented_count": 1,
        "no_artifact_change_count": 2,
    }


def test_semantic_progress_report_requires_run_manifest_checksum(tmp_path):
    reporter = _load_reporter()
    progress_path = tmp_path / "semantic_progress.json"
    progress_path.write_text(
        json.dumps(
            {
                "schema_version": "aworld.context.semantic-progress-evidence/v1",
                "status": "available",
                "counts": {
                    "goal_progress_count": 2,
                    "no_goal_progress_observation_count": 3,
                },
                "agents": [],
            }
        )
    )
    (tmp_path / "run_manifest.json").write_text(
        json.dumps(
            {
                "capture": {
                    "checksums": {
                        "semantic_progress.json": reporter.file_hash(progress_path)
                    }
                }
            }
        )
    )

    assert reporter.semantic_progress_summary(tmp_path)["counts"] == {
        "goal_progress_count": 2,
        "no_goal_progress_observation_count": 3,
    }

    progress_path.write_text(progress_path.read_text() + " ")
    assert reporter.semantic_progress_summary(tmp_path) == {
        "status": "unavailable",
        "reason": "semantic_progress_checksum_mismatch",
    }


def test_execution_depth_uses_partial_journals_without_promoting_reward(tmp_path):
    reporter = _load_reporter()
    (tmp_path / "raw_trajectory.partial.json").write_text(
        json.dumps(
            {
                "schema_version": "aworld.raw-trajectory.partial/v1",
                "completion_state": "incomplete",
                "calls": [{"status": "success"}] * 7,
                "tool_events": [
                    {"event_type": "sandbox_call_started"},
                    {"event_type": "sandbox_call_completed"},
                    {"event_type": "tool_observation_recorded"},
                ],
            }
        )
    )
    result = {
        "agent_exit_code": None,
        "reward": None,
        "failure": {"reason_code": "experiment_interrupted"},
        "context_metrics": {
            "partial_provider_call_count": 7,
            "partial_raw_trajectory_available": True,
        },
        "capture_recovery": {
            "tool_action_journal": {
                "event_type_counts": {
                    "sandbox_call_started": 1,
                    "sandbox_call_completed": 1,
                    "tool_observation_recorded": 1,
                }
            }
        },
    }

    summary = reporter.execution_depth_summary(tmp_path, result)

    assert summary["status"] == "available"
    assert summary["agent_completed"] is False
    assert summary["reward_available"] is False
    assert summary["model_round_count"] == 7
    assert summary["model_round_fidelity"] == "partial_journal"
    assert summary["tool_started_count"] == 1
    assert summary["tool_completed_count"] == 1
    assert summary["classification"] == "sustained_incomplete_progress_unavailable"
    assert summary["supports_quality_claim"] is False


def test_execution_depth_does_not_count_loop_budget_as_agent_completion(tmp_path):
    reporter = _load_reporter()
    (tmp_path / "provider_calls.json").write_text(
        json.dumps([{"request_id": "request", "status": "success"}])
    )
    progress_path = tmp_path / "semantic_progress.json"
    progress_path.write_text(
        json.dumps(
            {
                "schema_version": "aworld.context.semantic-progress-evidence/v1",
                "status": "available",
                "counts": {
                    "agent_step_count": 120,
                    "agent_loop_budget_exhausted_count": 1,
                    "goal_progress_count": 2,
                },
                "agents": [],
            }
        )
    )
    (tmp_path / "run_manifest.json").write_text(
        json.dumps(
            {
                "capture": {
                    "checksums": {
                        "semantic_progress.json": reporter.file_hash(progress_path)
                    }
                }
            }
        )
    )

    summary = reporter.execution_depth_summary(
        tmp_path,
        {"agent_exit_code": 0, "reward": 0, "context_metrics": {}},
    )

    assert summary["agent_process_completed"] is True
    assert summary["agent_completed"] is False
    assert summary["loop_budget_exhausted"] is True
    assert summary["classification"] == "budget_exhausted_with_typed_progress"


def test_paired_execution_depth_distinguishes_productive_growth_from_repetition():
    reporter = _load_reporter()
    common = {
        "experiment": "exp",
        "case_id": "case",
        "repeat": 1,
    }
    productive = reporter.paired_execution_depth_deltas(
        [
            {
                **common,
                "variant": "base",
                "summary": {
                    "status": "available",
                    "agent_completed": False,
                    "model_round_count": 4,
                    "tool_completed_count": 3,
                    "agent_step_count": 4,
                    "wall_time_seconds": 20.0,
                    "typed_progress": {
                        "status": "available",
                        "positive_count": 1,
                        "no_progress_count": 1,
                    },
                },
            },
            {
                **common,
                "variant": "candidate",
                "summary": {
                    "status": "available",
                    "agent_completed": True,
                    "model_round_count": 7,
                    "tool_completed_count": 6,
                    "agent_step_count": 7,
                    "wall_time_seconds": 35.0,
                    "typed_progress": {
                        "status": "available",
                        "positive_count": 4,
                        "no_progress_count": 1,
                    },
                },
            },
        ],
        baseline="base",
        candidate="candidate",
    )[0]

    assert productive["classification"] == "completion_improved"
    assert productive["model_round_count_delta"] == 3
    assert productive["typed_positive_progress_delta"] == 3
    assert productive["supports_quality_claim"] is False

    repeated_rows = json.loads(
        json.dumps(
            [
                {
                    **common,
                    "variant": "base",
                    "summary": {
                        "status": "available",
                        "agent_completed": False,
                        "model_round_count": 4,
                        "tool_completed_count": 3,
                        "agent_step_count": 4,
                        "wall_time_seconds": 20.0,
                        "typed_progress": {
                            "status": "available",
                            "positive_count": 1,
                            "no_progress_count": 1,
                        },
                    },
                },
                {
                    **common,
                    "variant": "candidate",
                    "summary": {
                        "status": "available",
                        "agent_completed": False,
                        "model_round_count": 9,
                        "tool_completed_count": 8,
                        "agent_step_count": 9,
                        "wall_time_seconds": 50.0,
                        "typed_progress": {
                            "status": "available",
                            "positive_count": 1,
                            "no_progress_count": 6,
                        },
                    },
                },
            ]
        )
    )
    repeated = reporter.paired_execution_depth_deltas(
        repeated_rows,
        baseline="base",
        candidate="candidate",
    )[0]
    assert repeated["classification"] == "no_progress_amplification"


def test_attribution_pairing_gate_detects_manifest_run_missing_after_ten_pairs():
    reporter = _load_reporter()
    summary = {
        "status": "available",
        "by_dimension": {
            "owner": {},
            "kind": {},
            "source_kind": {},
            "residency": {},
        },
    }
    legacy_summary = {**summary, "subject": "legacy_observed"}
    candidate_summary = {**summary, "subject": "candidate_selected"}
    case_ids = tuple(f"case-{index}" for index in range(11))
    rows = []
    for case_id in case_ids:
        rows.append(
            {
                "experiment": "exp",
                "run": f"{case_id}/legacy",
                "case_id": case_id,
                "repeat": 1,
                "variant": "legacy",
                "summary": legacy_summary,
            }
        )
        if case_id != "case-10":
            rows.append(
                {
                    "experiment": "exp",
                    "run": f"{case_id}/candidate",
                    "case_id": case_id,
                    "repeat": 1,
                    "variant": "candidate",
                    "summary": candidate_summary,
                }
            )

    status = reporter.provider_attribution_pairing_status(
        rows,
        experiment="exp",
        case_ids=case_ids,
        repeats=1,
        baseline="legacy",
        candidate="candidate",
    )

    assert status["status"] == "unavailable"
    assert status["available_pair_count"] == 10
    assert status["expected_pair_count"] == 11
    assert status["missing_run_count"] == 1
    assert status["reason"] == "provider_attribution_pairing_incomplete"


def test_attribution_pairing_gate_detects_duplicate_run():
    reporter = _load_reporter()
    summary = {
        "status": "available",
        "by_dimension": {
            "owner": {},
            "kind": {},
            "source_kind": {},
            "residency": {},
        },
    }
    legacy_summary = {**summary, "subject": "legacy_observed"}
    candidate_summary = {**summary, "subject": "candidate_selected"}
    rows = [
        {
            "experiment": "exp",
            "run": "legacy",
            "case_id": "case",
            "repeat": 1,
            "variant": "legacy",
            "summary": legacy_summary,
        },
        {
            "experiment": "exp",
            "run": "candidate",
            "case_id": "case",
            "repeat": 1,
            "variant": "candidate",
            "summary": candidate_summary,
        },
        {
            "experiment": "exp",
            "run": "candidate-duplicate",
            "case_id": "case",
            "repeat": 1,
            "variant": "candidate",
            "summary": candidate_summary,
        },
    ]

    status = reporter.provider_attribution_pairing_status(
        rows,
        experiment="exp",
        case_ids=("case",),
        repeats=1,
        baseline="legacy",
        candidate="candidate",
    )

    assert status["status"] == "unavailable"
    assert status["duplicate_run_count"] == 1
    assert status["reason"] == "provider_attribution_pairing_incomplete"


def test_attribution_pairing_ignores_other_declared_ablation_arms():
    reporter = _load_reporter()
    common = {
        "status": "available",
        "by_dimension": {
            "owner": {},
            "kind": {},
            "source_kind": {},
            "residency": {},
        },
        "dimension_resolution": {
            "owner": "compiler_owner_v1",
            "kind": "provider_occurrence_kind_v1",
            "source_kind": "provider_occurrence_source_v1",
            "residency": "compiler_logical_residency_v1",
        },
    }
    rows = [
        {
            "experiment": "exp",
            "run": "baseline",
            "case_id": "case",
            "repeat": 1,
            "variant": "baseline",
            "summary": {**common, "subject": "legacy_observed"},
        },
        {
            "experiment": "exp",
            "run": "candidate",
            "case_id": "case",
            "repeat": 1,
            "variant": "candidate",
            "summary": {**common, "subject": "candidate_selected"},
        },
        {
            "experiment": "exp",
            "run": "third",
            "case_id": "case",
            "repeat": 1,
            "variant": "third",
            "summary": {**common, "subject": "candidate_selected"},
        },
    ]

    status = reporter.provider_attribution_pairing_status(
        rows,
        experiment="exp",
        case_ids=("case",),
        repeats=1,
        baseline="baseline",
        candidate="candidate",
    )

    assert status["status"] == "available"
    assert status["actual_run_count"] == 2
