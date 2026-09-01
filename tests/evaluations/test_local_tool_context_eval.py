from __future__ import annotations

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
