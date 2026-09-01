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
    expected.write_text(
        json.dumps({"artifact": "result.json", "exact": {"answer": 7}})
    )
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


def test_benefit_report_consumes_real_artifact_contract_and_stays_not_ready_for_smoke(tmp_path):
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
            }
        )
    )
    results = []
    for name, reward, tokens in (("legacy", 0, 100), ("candidate", 1, 60)):
        run = experiment / "runs" / "case" / name / "repeat-01"
        run.mkdir(parents=True)
        provider_payload = {"model": "test", "messages": [{"role": "user", "content": "x"}]}
        provider_call = {
            "provider_invoked": True,
            "request_trace_match": True,
            "usage_normalized": {
                "prompt_tokens": tokens,
                "completion_tokens": 1,
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
    assert report["combined_benefit"]["metric_means"]["prompt_tokens"] == -40.0
    assert report["default_on_readiness"]["status"] == "not_ready"
    assert "insufficient_paired_evidence" in report["default_on_readiness"]["gate_failures"]
    assert "cross_workload_evidence_missing" in report["default_on_readiness"]["gate_failures"]
    assert "provider_attribution_pairing_incomplete" in report["default_on_readiness"]["gate_failures"]


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


def _turn_receipt(reporter, kind, cause, identity, parent=None):
    return {
        "schema_version": "aworld.context.turn-economics.v1",
        "task_epoch": 0,
        "turn_kind": kind,
        "cause": cause,
        "cause_supported": True,
        "turn_id_hash": reporter.value_hash({f"{kind}_turn": identity}),
        "request_id_hash": reporter.value_hash({"request_id": identity}) if kind == "model" else None,
        "tool_call_id_hash": reporter.value_hash({"tool_call_id": identity}) if kind == "tool" else None,
        "parent_turn_id_hash": parent,
        "evidence_hash": None,
    }


def _retrieval_content():
    chunk = "x" * 256
    return {
        "type": "text",
        "content": chunk,
        "artifact_ref": "opaque-ref",
        "offset": 4096,
        "next_offset": 4352,
        "returned_bytes": 256,
        "total_bytes": 131072,
        "content_sha256": "sha256:" + "1" * 64,
        "chunk_sha256": "sha256:" + hashlib.sha256(chunk.encode()).hexdigest(),
        "complete": False,
    }


def _retrieval_receipt(reporter, *, consumed):
    content = _retrieval_content()
    result_hash = reporter.value_hash(content)
    plan = {
        "schema_version": "aworld.context.artifact-retrieval-plan.v1",
        "owner_code": reporter.value_hash({"owner_tool": "generic_stream"}),
        "action_code": reporter.value_hash({"retrieval_action": "read_output_artifact"}),
        "artifact_ref_hash": reporter.value_hash({"artifact_ref": "opaque-ref"}),
        "artifact_content_hash": "sha256:" + "1" * 64,
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
        "source_content_hash": "sha256:" + "1" * 64,
        "result_content_hash": result_hash,
        "complete": False,
        "next_request_id_hash": reporter.value_hash({"request_id": "after"}) if consumed else None,
        "consumed_content_hash": result_hash if consumed else None,
        "consumed": consumed,
    }
    return value


def test_turn_artifact_economics_uses_only_typed_truth(tmp_path):
    reporter = _load_reporter()
    artifact_bytes = b"x" * 131072
    tool_retrieval = _retrieval_receipt(reporter, consumed=False)
    provider_consumption = _retrieval_receipt(reporter, consumed=True)
    raw = [{"state": {"input": {"action_result": [
        {
            "tool_call_id": "retrieve",
            "tool_name": "generic_stream",
            "action_name": "read_output_artifact",
            "content": _retrieval_content(),
            "metadata": {
                "turn_economics": _turn_receipt(
                    reporter, "tool", "artifact_retrieval", "retrieve",
                    parent=reporter.value_hash({"model_turn": "after"}),
                ),
                "tool_output_policy": {
                    "policy_version": "v1",
                    "raw_byte_count": 131072,
                    "raw_checksum": "sha256:" + hashlib.sha256(artifact_bytes).hexdigest(),
                    "inline_tokens": 128,
                    "offloaded_tokens": 32640,
                    "artifact_ref": "opaque",
                    "context_artifact_ref": "opaque-context",
                    "context_artifact_role": "audit_snapshot",
                    "upstream_artifacts": [{
                        "ref": "opaque",
                        "content_hash": "sha256:" + "1" * 64,
                        "byte_count": 131072,
                        "owner_tool": "generic_stream",
                        "retrieval_action": "read_output_artifact",
                    }],
                },
                "artifact_retrieval": tool_retrieval,
            },
        }
    ]}}}]
    calls = [{
        "request_id": "after",
        "turn_economics": _turn_receipt(
            reporter, "model", "artifact_retrieval", "after",
            parent=reporter.value_hash({"tool_turn": "retrieve"}),
        ),
        "artifact_retrieval_consumption": [provider_consumption],
    }]
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(artifact_bytes)

    summary = reporter.turn_artifact_economics_summary(calls, raw, [artifact])

    assert summary["turn_causes"]["status"] == "available"
    assert summary["turn_causes"]["counts"]["artifact_retrieval"] == {"model": 1, "tool": 1}
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
    replayed = reporter.turn_artifact_economics_summary(
        calls, replayed_raw, [artifact]
    )
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
        {"experiment": "generic", "case_id": "noisy", "repeat": 1, "variant": variant, "summary": summary}
        for variant in ("legacy", "candidate")
    ]
    delta = reporter.paired_turn_artifact_deltas(
        runs, baseline="legacy", candidate="candidate"
    )[0]
    assert delta["status"] == "available"
    assert delta["candidate_minus_baseline"]["retrieved_bytes"] == 0


def test_turn_artifact_economics_missing_receipts_is_unavailable_not_heuristic():
    reporter = _load_reporter()
    calls = [{"request": {"messages": [{"content": "read_output_artifact retry validation"}]}}]
    raw = [{"action_result": [{"content": "artifact retrieved", "metadata": {}}]}]

    summary = reporter.turn_artifact_economics_summary(calls, raw, [])

    assert summary["turn_causes"]["status"] == "unavailable"
    assert summary["turn_causes"]["counts"] == {}
    assert summary["tool_outputs"]["status"] == "unavailable"
    assert summary["retrieval"]["status"] == "not_applicable"
    assert summary["retrieval"]["opportunity_count"] == 0


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
                "provider_lowering": {"candidate_content_hash": candidate_hash, "attribution": receipt},
            },
        },
        {
            "request": {
                "messages": [
                    {"role": "system", "content": "must-not-be-classified"}
                ]
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


def test_benefit_report_marks_all_missing_attribution_unavailable():
    reporter = _load_reporter()

    summary = reporter.provider_attribution_summary(
        [{"request": {"messages": [{"role": "user", "content": "secret"}]}}]
    )

    assert summary["status"] == "unavailable"
    assert summary["available_receipt_count"] == 0
    assert summary["reason"] == "provider_attribution_incomplete"
    assert summary["by_dimension"]["owner"] == {}


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
        "entries": [{
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
        }],
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
    calls = [{
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
    }]

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
        {"experiment": "exp-a", "run": "legacy-run", "case_id": "case", "repeat": 0, "variant": "legacy", "summary": legacy_available},
        {"experiment": "exp-a", "run": "candidate-run", "case_id": "case", "repeat": 0, "variant": "candidate", "summary": candidate_available},
        {"experiment": "exp-b", "run": "candidate-only", "case_id": "case", "repeat": 0, "variant": "candidate", "summary": candidate_available},
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
        "owner": {}, "kind": {}, "source_kind": {}, "residency": {},
    }
    wrong = {"status": "available", "subject": "candidate_selected", "by_dimension": dimensions}
    rows = [
        {"experiment": "exp", "run": "legacy", "case_id": "case", "repeat": 1, "variant": "legacy", "summary": wrong},
        {"experiment": "exp", "run": "candidate", "case_id": "case", "repeat": 1, "variant": "candidate", "summary": wrong},
    ]

    delta = reporter.paired_attribution_deltas(
        rows, baseline="legacy", candidate="candidate"
    )[0]

    assert delta["status"] == "unsupported"
    assert delta["reason"] == "paired_attribution_subject_mismatch"


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
    delta = reporter.paired_attribution_deltas([
        {"experiment": "exp", "run": "legacy", "case_id": "case", "repeat": 1, "variant": "legacy", "summary": legacy},
        {"experiment": "exp", "run": "candidate", "case_id": "case", "repeat": 1, "variant": "candidate", "summary": candidate},
    ], baseline="legacy", candidate="candidate")[0]

    assert delta["status"] == "available"
    assert delta["total_canonical_bytes_delta"] == -10
    assert delta["by_dimension_delta"]["owner"] is None
    assert delta["by_dimension_delta"]["residency"] is None
    assert delta["dimension_status"]["owner"]["reason"] == "resolution_mismatch"
    assert delta["dimension_status"]["kind"]["status"] == "available"


def test_attribution_pairing_gate_detects_manifest_run_missing_after_ten_pairs():
    reporter = _load_reporter()
    summary = {
        "status": "available",
        "by_dimension": {
            "owner": {}, "kind": {}, "source_kind": {}, "residency": {},
        },
    }
    legacy_summary = {**summary, "subject": "legacy_observed"}
    candidate_summary = {**summary, "subject": "candidate_selected"}
    case_ids = tuple(f"case-{index}" for index in range(11))
    rows = []
    for case_id in case_ids:
        rows.append({
            "experiment": "exp", "run": f"{case_id}/legacy", "case_id": case_id,
            "repeat": 1, "variant": "legacy", "summary": legacy_summary,
        })
        if case_id != "case-10":
            rows.append({
                "experiment": "exp", "run": f"{case_id}/candidate", "case_id": case_id,
                "repeat": 1, "variant": "candidate", "summary": candidate_summary,
            })

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
            "owner": {}, "kind": {}, "source_kind": {}, "residency": {},
        },
    }
    legacy_summary = {**summary, "subject": "legacy_observed"}
    candidate_summary = {**summary, "subject": "candidate_selected"}
    rows = [
        {"experiment": "exp", "run": "legacy", "case_id": "case", "repeat": 1, "variant": "legacy", "summary": legacy_summary},
        {"experiment": "exp", "run": "candidate", "case_id": "case", "repeat": 1, "variant": "candidate", "summary": candidate_summary},
        {"experiment": "exp", "run": "candidate-duplicate", "case_id": "case", "repeat": 1, "variant": "candidate", "summary": candidate_summary},
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
