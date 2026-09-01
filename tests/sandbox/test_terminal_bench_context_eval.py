from __future__ import annotations

import importlib.util
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load_example(name: str):
    path = ROOT / "examples" / "sandbox" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_variant_contract_rejects_prompt_or_task_specific_fields(tmp_path):
    runner = _load_example("docker_terminal_bench")
    variant = tmp_path / "variant.json"
    variant.write_text(
        json.dumps(
            {
                "name": "score-tuned",
                "system_prompt": "special answer",
                "agent_memory_config": {},
                "docker_output_policy": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unexpected fields"):
        runner._load_variant(variant)


def test_variant_contract_accepts_context_policy_only(tmp_path):
    runner = _load_example("docker_terminal_bench")
    variant = tmp_path / "variant.json"
    variant.write_text(
        json.dumps(
            {
                "schema_version": "aworld.context-eval-variant/v1",
                "name": "candidate",
                "agent_memory_config": {
                    "tool_result_offload": True,
                    "tool_result_length_threshold": 4096,
                },
                "context_compiler": {
                    "mode": "enforce",
                    "universal_final": True,
                    "progressive_tools": True,
                    "artifact_offload": True,
                    "completion_contract": "enforce",
                },
                "docker_output_policy": {
                    "max_inline_output_bytes": 8192,
                    "output_head_bytes": 4096,
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = runner._load_variant(variant)
    assert loaded["name"] == "candidate"
    assert loaded["context_compiler"]["mode"] == "enforce"


def test_variant_contract_rejects_unknown_context_compiler_fields(tmp_path):
    runner = _load_example("docker_terminal_bench")
    variant = tmp_path / "variant.json"
    variant.write_text(
        json.dumps(
            {
                "name": "task-tuned-compiler",
                "context_compiler": {"expected_answer": "special answer"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="context_compiler contains unsupported fields"):
        runner._load_variant(variant)


def test_llm_call_capture_falls_back_to_live_context_for_blocked_calls():
    runner = _load_example("docker_terminal_bench")

    class Response:
        llm_calls = []

    class LiveContext:
        def get_llm_calls(self):
            return [{"status": "blocked_before_provider"}]

    class Agent:
        context = LiveContext()

    calls, source, continuity = runner._resolve_llm_call_capture(
        Response(), Agent()
    )

    assert calls == [{"status": "blocked_before_provider"}]
    assert source == "live_context_fallback"
    assert continuity == {
        "task_response_count": 0,
        "live_context_count": 1,
        "counts_match": False,
        "task_response_sha256": runner._llm_calls_digest([]),
        "live_context_sha256": runner._llm_calls_digest(
            [{"status": "blocked_before_provider"}]
        ),
        "snapshots_match": False,
    }


def test_provider_bound_gate_uses_lowering_receipt_not_model_capture_stage():
    runner = _load_example("docker_terminal_bench")
    call = {
        "capture_stage": "model_boundary",
        "provider_invoked": True,
        "context_rollout": {
            "candidate_applied": True,
            "provider_lowering": {
                "provider_request": {
                    "capture_stage": "provider_prepared",
                    "fidelity": "provider_prepared",
                }
            },
        },
    }

    assert runner._is_provider_bound_call(call) is True
    call["provider_invoked"] = False
    assert runner._is_provider_bound_call(call) is False


def test_provider_bound_gate_accepts_provider_owned_off_mode_capture():
    runner = _load_example("docker_terminal_bench")
    call = {
        "provider_invoked": True,
        "provider_request": {
            "capture_stage": "provider_prepared",
            "fidelity": "provider_prepared",
            "payload": {"model": "test", "messages": []},
        },
    }

    assert runner._is_provider_bound_call(call) is True


def test_dataset_adapter_extracts_generic_task_archive(tmp_path):
    harness = _load_example("terminal_bench_context_eval")
    archive_buffer = io.BytesIO()
    files = {
        "sample/task.toml": b'[task]\nname="terminal-bench/sample"\n[environment]\ndocker_image="sample:1"\n',
        "sample/instruction.md": b"Do the task",
        "sample/environment/Dockerfile": b"FROM alpine:3.20\nWORKDIR /workspace\n",
        "sample/tests/test.sh": b"#!/bin/sh\necho 1 > /logs/verifier/reward.txt\n",
    }
    with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))

    dataset = tmp_path / "dataset.zip"
    with zipfile.ZipFile(dataset, "w") as package:
        package.writestr("tasks/sample.tar.gz", archive_buffer.getvalue())

    fixture = harness.extract_task(dataset, "sample", tmp_path / "fixture")

    assert fixture.name == "sample"
    assert fixture.instruction.read_text() == "Do the task"
    assert fixture.environment.joinpath("Dockerfile").exists()
    assert fixture.tests.joinpath("test.sh").exists()


def test_summary_pairs_reward_with_context_effects():
    harness = _load_example("terminal_bench_context_eval")
    results = [
        {
            "task": "generic-task",
            "variant": "legacy",
            "repetition": 1,
            "reward": "0",
            "context_metrics": {
                "provider_truth_available": True,
                "provider_request_bytes": 1000,
                "prompt_tokens": 100,
                "offloaded_artifact_bytes": 0,
            },
        },
        {
            "task": "generic-task",
            "variant": "candidate",
            "repetition": 1,
            "reward": "1",
            "context_metrics": {
                "provider_truth_available": True,
                "provider_request_bytes": 700,
                "prompt_tokens": 70,
                "offloaded_artifact_bytes": 500,
            },
        },
    ]

    summary = harness.summarize_results(results, "legacy")

    assert summary["paired_deltas"] == [
        {
            "task": "generic-task",
            "repetition": 1,
            "baseline": "legacy",
            "candidate": "candidate",
            "reward_delta": 1.0,
            "provider_request_bytes_delta": -300,
            "prompt_tokens_delta": -30,
            "offloaded_artifact_bytes_delta": 500,
        }
    ]


def test_python_functions_verifier_mode_is_explicit_and_not_a_variant_field(tmp_path, monkeypatch):
    harness = _load_example("terminal_bench_context_eval")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "terminal_bench_context_eval.py",
            "--dataset",
            str(tmp_path / "dataset.zip"),
            "--task",
            "sample",
            "--output-dir",
            str(tmp_path / "out"),
            "--verifier-mode",
            "python-functions",
        ],
    )

    args = harness.parse_args()

    assert args.verifier_mode == "python-functions"
