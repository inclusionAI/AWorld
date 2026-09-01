from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from types import SimpleNamespace
import zipfile
from pathlib import Path

import pytest

from aworld.core.context.base import Context


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
        "reconciled_count": 1,
        "reconciled_sha256": runner._llm_calls_digest(
            [{"status": "blocked_before_provider"}]
        ),
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


def test_capture_reconciliation_preserves_live_retry_attempts_for_diagnostics():
    runner = _load_example("docker_terminal_bench")
    response = SimpleNamespace(
        llm_calls=[
            {"call_id": "agent-call", "request_id": "request-1", "status": "started"}
        ]
    )
    live_calls = [
        {"call_id": "agent-call", "request_id": "request-1", "status": "success"},
        {"call_id": "agent-call", "request_id": "request-2", "status": "blocked"},
    ]
    agent = SimpleNamespace(
        context=SimpleNamespace(get_llm_calls=lambda: live_calls)
    )

    calls, source, continuity = runner._resolve_llm_call_capture(response, agent)

    assert source == "reconciled_task_response_live_context"
    assert [call["request_id"] for call in calls] == ["request-1", "request-2"]
    assert calls[0]["status"] == "success"
    assert continuity["snapshots_match"] is False
    assert continuity["reconciled_count"] == 2


def test_lifecycle_evidence_is_typed_hashed_and_privacy_safe():
    runner = _load_example("docker_terminal_bench")
    context = Context(task_id="private-task", session_id="private-session")
    agent = type("Agent", (), {"context": context})()

    evidence = runner._context_lifecycle_evidence(agent)

    assert evidence["status"] == "available"
    assert evidence["state_hash"].startswith("sha256:")
    assert evidence["state"]["session_id_hash"].startswith("sha256:")
    assert evidence["state"]["branch_id_hash"].startswith("sha256:")
    assert "private-session" not in repr(evidence)
    assert "private-task" not in repr(evidence)


def test_dataset_adapter_extracts_generic_task_archive(tmp_path, monkeypatch):
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

    output_dir = tmp_path / "dry-run"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "terminal_bench_context_eval.py",
            "--dataset",
            str(dataset),
            "--task",
            "sample",
            "--output-dir",
            str(output_dir),
            "--build-timeout-sec",
            "1800",
            "--dry-run",
        ],
    )
    harness.main()
    manifest = json.loads(
        (output_dir / "experiment_manifest.json").read_text(encoding="utf-8")
    )
    plan = manifest["image_build_plans"]["sample"]
    assert plan["build_timeout_source"] == "cli_override"
    assert plan["effective_build_timeout_sec"] == 1800.0
    assert plan["build_context_sha256"]
    assert manifest["image_resolution"] == {
        "status": "not_attempted",
        "images": {},
    }


def test_packaged_image_build_timeout_can_override_dataset_default(tmp_path, monkeypatch):
    harness = _load_example("terminal_bench_context_eval")
    environment = tmp_path / "environment"
    environment.mkdir()
    environment.joinpath("Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    fixture = harness.TaskFixture(
        name="sample",
        root=tmp_path,
        archive_sha256="0" * 64,
        config={"environment": {"build_timeout_sec": 600}},
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            1 if command[1:3] == ["image", "inspect"] else 0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(harness, "run_command", fake_run)

    image = harness.docker_image_for_task(
        fixture,
        "docker",
        use_declared_image=False,
        build_timeout_sec=1800,
    )

    assert image.startswith("aworld-context-eval:")
    assert calls[-1][1]["timeout"] == 1800
    plan = harness.docker_image_build_plan(
        fixture,
        use_declared_image=False,
        build_timeout_sec=1800,
    )
    assert plan["effective_build_timeout_sec"] == 1800.0
    assert plan["build_timeout_source"] == "cli_override"
    assert plan["build_context_sha256"]


def test_packaged_image_identity_covers_complete_build_context(tmp_path):
    harness = _load_example("terminal_bench_context_eval")
    environment = tmp_path / "environment"
    environment.mkdir()
    environment.joinpath("Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    copied = environment / "payload.txt"
    copied.write_text("first", encoding="utf-8")
    fixture = harness.TaskFixture(
        name="sample",
        root=tmp_path,
        archive_sha256="0" * 64,
        config={"environment": {"build_timeout_sec": 600}},
    )

    first = harness.docker_image_build_plan(
        fixture, use_declared_image=False, build_timeout_sec=None
    )
    copied.write_text("second", encoding="utf-8")
    second = harness.docker_image_build_plan(
        fixture, use_declared_image=False, build_timeout_sec=None
    )

    assert first["build_context_sha256"] != second["build_context_sha256"]
    assert first["image_ref"] != second["image_ref"]
    assert first["effective_build_timeout_sec"] == 600.0
    assert first["build_timeout_source"] == "dataset"


def test_dataset_build_timeout_is_validated_and_timeout_error_is_redacted(
    tmp_path, monkeypatch
):
    harness = _load_example("terminal_bench_context_eval")
    environment = tmp_path / "environment"
    environment.mkdir()
    environment.joinpath("Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    fixture = harness.TaskFixture(
        name="sample",
        root=tmp_path,
        archive_sha256="0" * 64,
        config={"environment": {"build_timeout_sec": float("nan")}},
    )
    with pytest.raises(ValueError, match="positive finite"):
        harness.docker_image_build_plan(
            fixture, use_declared_image=False, build_timeout_sec=None
        )

    fixture.config["environment"]["build_timeout_sec"] = 600

    def fake_run(command, **kwargs):
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        raise subprocess.TimeoutExpired(
            command,
            kwargs["timeout"],
            stderr="registry-secret-must-not-leak",
        )

    monkeypatch.setattr(harness, "run_command", fake_run)
    with pytest.raises(RuntimeError, match="timed out after 600 seconds") as raised:
        harness.docker_image_for_task(
            fixture,
            "docker",
            use_declared_image=False,
            build_timeout_sec=None,
        )
    assert "registry-secret" not in str(raised.value)


def test_partial_timeout_evidence_is_typed_unavailable(tmp_path):
    harness = _load_example("terminal_bench_context_eval")
    tmp_path.joinpath("provider_calls.json").write_text("[{", encoding="utf-8")
    tmp_path.joinpath("raw_trajectory.json").write_text("[", encoding="utf-8")
    tmp_path.joinpath("run_manifest.json").write_text("{", encoding="utf-8")

    metrics = harness.collect_context_metrics(tmp_path)

    assert metrics["provider_truth_available"] is False
    assert metrics["raw_trajectory_available"] is False
    assert metrics["capture_integrity_available"] is False
    assert metrics["evidence_parse_error_count"] == 3
    assert metrics["evidence_parse_error_reason_codes"] == [
        "provider_calls_malformed",
        "raw_trajectory_malformed",
        "run_manifest_malformed",
    ]


def test_timeout_output_accepts_text_and_bytes():
    harness = _load_example("terminal_bench_context_eval")

    text_timeout = subprocess.TimeoutExpired(
        ["command"], 1, output="partial stdout", stderr="partial stderr"
    )
    bytes_timeout = subprocess.TimeoutExpired(
        ["command"], 1, output=b"byte stdout", stderr=b"byte stderr"
    )

    assert harness.timeout_output(text_timeout, "stdout") == "partial stdout"
    assert harness.timeout_output(text_timeout, "stderr") == "partial stderr"
    assert harness.timeout_output(bytes_timeout, "stdout") == "byte stdout"
    assert harness.timeout_output(bytes_timeout, "stderr") == "byte stderr"


def test_agent_timeout_is_persisted_as_typed_incomplete_result(tmp_path, monkeypatch):
    harness = _load_example("terminal_bench_context_eval")
    root = tmp_path / "fixture"
    root.joinpath("environment").mkdir(parents=True)
    root.joinpath("tests").mkdir()
    root.joinpath("instruction.md").write_text("do the task", encoding="utf-8")
    fixture = harness.TaskFixture(
        name="sample",
        root=root,
        archive_sha256="a" * 64,
        config={"environment": {}, "agent": {"timeout_sec": 1}},
    )

    def fake_run(command, **kwargs):
        if command[0] == sys.executable:
            raise subprocess.TimeoutExpired(
                command, kwargs["timeout"], output="partial", stderr="timed out"
            )
        return subprocess.CompletedProcess(command, 0, stdout="image-id\n", stderr="")

    monkeypatch.setattr(harness, "run_command", fake_run)

    result = harness.execute_job(
        docker="docker",
        fixture=fixture,
        image="sample:1",
        variant_name="legacy",
        variant_path=None,
        repetition=1,
        output_dir=tmp_path / "output",
        max_steps=1,
        keep_container=False,
        verifier_mode="python-functions",
    )

    run_dir = tmp_path / "output" / "runs" / "sample" / "legacy" / "repeat-01"
    assert result["reward"] is None
    assert result["failure"] == {
        "stage": "agent",
        "reason_code": "agent_timeout",
        "timeout_sec": 61.0,
    }
    assert json.loads(run_dir.joinpath("result.json").read_text())["failure"] == result["failure"]
    assert run_dir.joinpath("agent.stdout.log").read_text() == "partial"
    assert run_dir.joinpath("agent.stderr.log").read_text() == "timed out"


def test_verifier_timeout_is_persisted_and_excluded_from_pairs(tmp_path, monkeypatch):
    harness = _load_example("terminal_bench_context_eval")
    root = tmp_path / "fixture"
    root.joinpath("environment").mkdir(parents=True)
    root.joinpath("tests").mkdir()
    root.joinpath("instruction.md").write_text("do the task", encoding="utf-8")
    fixture = harness.TaskFixture(
        name="sample",
        root=root,
        archive_sha256="a" * 64,
        config={"environment": {}, "verifier": {"timeout_sec": 2}},
    )

    def fake_run(command, **kwargs):
        if command[0] == sys.executable:
            return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")
        if command[:2] == ["docker", "exec"]:
            raise subprocess.TimeoutExpired(
                command, kwargs["timeout"], output="checking", stderr="too slow"
            )
        return subprocess.CompletedProcess(command, 0, stdout="image-id\n", stderr="")

    monkeypatch.setattr(harness, "run_command", fake_run)
    result = harness.execute_job(
        docker="docker",
        fixture=fixture,
        image="sha256:" + "b" * 64,
        variant_name="candidate",
        variant_path=None,
        repetition=1,
        output_dir=tmp_path / "output",
        max_steps=1,
        keep_container=False,
        verifier_mode="python-functions",
    )

    run_dir = tmp_path / "output" / "runs" / "sample" / "candidate" / "repeat-01"
    assert result["reward"] is None
    assert result["failure"] == {
        "stage": "verifier",
        "reason_code": "verifier_timeout",
        "timeout_sec": 2.0,
    }
    assert run_dir.joinpath("verifier", "stdout.log").read_text() == "checking"
    assert json.loads(run_dir.joinpath("result.json").read_text())["failure"] == result["failure"]
    assert harness.summarize_results(
        [result], baseline_variant="candidate"
    )["paired_deltas"] == []


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
