from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

import aworld.evaluations.substrate as substrate_module
import aworld_cli.evaluator_runtime as evaluator_runtime_module
from aworld.config.conf import ModelConfig
from aworld.evaluations.manifests import get_declared_eval_suite_schema
from aworld.evaluations.report import EvaluatorReport
from aworld_cli.evaluator_runtime import (
    _CliAgentRuntimeHarness,
    _artifact_backed_evidence_index,
    _build_source_suite,
    _build_source_prompt,
    _build_trajectory_prompt,
    _load_prompt_evidence_bundle,
    available_evaluator_suites,
    evaluator_exit_code,
    get_declared_evaluator_suite_schema,
    get_evaluator_report_schema,
    run_evaluator_cli,
    run_evaluator_source_cli,
    validate_evaluator_report,
)
from aworld_cli.evaluator_rendering import render_evaluator_summary


def _write_answer_source(path: Path) -> None:
    path.write_text('{"id":"case-1","input":"question","answer":"existing"}\n', encoding="utf-8")


def _write_verified_evidence_bundle(
    bundle_path: Path,
    entries: list[dict[str, object]],
    *,
    manifest_path: Path | None = None,
    manifest_entries: list[dict[str, object]] | None = None,
) -> tuple[Path, str]:
    manifest_path = manifest_path or bundle_path.with_name("evidence_manifest.jsonl")
    if manifest_entries is None:
        manifest_entries = []
        for entry in entries:
            manifest_entry: dict[str, object] = {
                "source_id": entry["source_id"],
                "extraction_method": entry["extraction_method"],
            }
            if entry.get("evidence_type") == "metadata":
                manifest_entry["evidence_type"] = "metadata"
                manifest_entry["metadata"] = entry["metadata"]
            else:
                manifest_entry["artifact_path"] = entry["artifact_path"]
            bounded_evidence = entry.get("bounded_evidence")
            if isinstance(bounded_evidence, dict):
                manifest_entry.update(bounded_evidence)
            manifest_entries.append(manifest_entry)
    manifest_payload = (
        "".join(
            json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
            for entry in manifest_entries
        )
    ).encode("utf-8")
    manifest_path.write_bytes(manifest_payload)
    manifest_fingerprint = "sha256:" + hashlib.sha256(manifest_payload).hexdigest()
    bundle_path.write_text(
        json.dumps(
            {
                "format": "aworld.self_evolve.evidence_bundle",
                "version": 1,
                "valid": True,
                "manifest_path": str(manifest_path),
                "manifest": {
                    "path": str(manifest_path),
                    "present": True,
                    "readable": True,
                    "valid": True,
                    "entry_count": len(manifest_entries),
                    "invalid_entry_count": 0,
                    "size_bytes": len(manifest_payload),
                    "fingerprint": manifest_fingerprint,
                },
                "entries": entries,
            }
        ),
        encoding="utf-8",
    )
    return manifest_path, manifest_fingerprint


def _verified_evidence_prompt_builder(
    root: Path,
    *,
    source_id: str,
):
    root.mkdir(parents=True, exist_ok=True)
    source_path = root / "source.txt"
    source_path.write_text(
        f"verified evidence for {source_id}",
        encoding="utf-8",
    )
    bundle_path = root / "evidence_bundle.json"
    _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": source_id,
                "artifact_path": str(source_path),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {
                    "bounded_excerpt": f"verified evidence for {source_id}"
                },
            }
        ],
    )

    def build(case_input, target, suite):
        return _build_trajectory_prompt(
            {"input": "question"},
            {
                "case_id": source_id,
                "answer": "answer",
                "trajectory": [],
                "artifacts": {"outcome": {"extracted_path": None}},
                "evidence_bundle_path": str(bundle_path),
            },
            suite=None,
        )

    return build


@pytest.fixture(autouse=True)
def _reset_eval_registry_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(substrate_module, "_EVAL_SUITE_REGISTRY", {})
    monkeypatch.setattr(substrate_module, "_LOADED_EVAL_MANIFEST_PATHS", set())
    monkeypatch.setattr(substrate_module, "_DECLARED_EVAL_SUITE_IDS_BY_WORKSPACE", {})
    monkeypatch.setattr(
        evaluator_runtime_module,
        "_VERIFIED_EVIDENCE_SNAPSHOT_ROOT",
        tmp_path / "evaluator-private-snapshots",
        raising=False,
    )
    monkeypatch.setattr(
        evaluator_runtime_module,
        "_ACTIVE_VERIFIED_EVIDENCE_SESSIONS",
        {},
    )
    monkeypatch.setattr(
        evaluator_runtime_module,
        "_VERIFIED_EVIDENCE_STALE_RECLAIMED",
        False,
    )
    monkeypatch.setattr(
        substrate_module,
        "_PRIVATE_ARTIFACT_SESSION_CLEANUPS",
        {},
    )
    substrate_module.register_eval_suite(
        "app-evaluator",
        lambda target: substrate_module.get_builtin_eval_suite("app-evaluator"),
        matcher=lambda target: target.get("target_kind") in {"file", "directory", "image"},
        priority=10,
    )


def test_run_evaluator_cli_persists_approval_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")
    output = tmp_path / "report.json"

    async def fake_run_evaluation_flow(flow):
        return {
            "report_version": 1,
            "suite_id": "app-evaluator",
            "judge_backend": {"backend_id": "stub-agent"},
            "summary": {"app-evaluator": {"score": {"mean": 0.7}}},
            "results": [],
            "gate": {"status": "needs_approval", "metric_name": "score", "value": 0.7},
            "approval": {"required": True, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)
    monkeypatch.setattr("builtins.input", lambda _: "y")

    report = run_evaluator_cli(
        target=str(target),
        interactive_approval=True,
        output=str(output),
    )

    persisted = json.loads(output.read_text(encoding="utf-8"))

    assert report["approval"]["resolved"] is True
    assert report["approval"]["approved"] is True
    assert persisted["approval"]["approved"] is True
    assert persisted["judge_backend"]["backend_id"] == "stub-agent"


def test_run_evaluator_source_cli_builds_task_answer_flow_with_default_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "answers.jsonl"
    _write_answer_source(input_path)
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")
    output = tmp_path / "report.json"
    captured = {}

    async def fake_run_evaluation_flow(flow):
        captured["flow"] = flow
        return {
            "report_version": 1,
            "suite_id": "answer-source-evaluator",
            "judge_backend": {"backend_id": "source-agent-md"},
            "summary": {"answer-source-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_source_cli(
        input=str(input_path),
        kind="answer",
        judge_agent=str(judge_agent),
        output=str(output),
        judge_timeout_seconds=12.5,
    )

    flow = captured["flow"]
    assert flow.target["target_kind"] == "source"
    assert flow.target["source_kind"] == "answer"
    assert flow.suite.cases[0].case_id == "case-1"
    assert flow.suite.cases[0].input == {"input": "question"}
    assert flow.suite.judge_backend.backend_id == "source-agent-md"
    assert flow.suite.judge_backend.timeout_seconds == 12.5
    assert report["source_selection"]["kind"] == "answer"
    assert report["source_selection"]["judge_timeout_seconds"] == 12.5
    assert report["automation"]["source_kind"] == "answer"
    assert output.exists()


def test_run_evaluator_source_cli_uses_judge_model_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "answers.jsonl"
    _write_answer_source(input_path)
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")
    captured = {}

    monkeypatch.setattr(
        "aworld_cli.core.model_profiles.resolve_model_profile",
        lambda profile: ModelConfig(
            llm_provider="anthropic",
            llm_model_name=f"{profile}-model",
            llm_api_key="profile-key",
        ),
    )

    async def fake_run_evaluation_flow(flow):
        captured["backend"] = flow.suite.judge_backend
        return {
            "report_version": 1,
            "suite_id": "answer-source-evaluator",
            "summary": {"answer-source-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_source_cli(
        input=str(input_path),
        kind="answer",
        judge_agent=str(judge_agent),
        judge_model_profile="judge",
    )

    model_config = captured["backend"].model_config
    assert model_config.llm_provider == "anthropic"
    assert model_config.llm_model_name == "judge-model"
    assert report["source_selection"]["judge_model_profile"] == "judge"


def test_run_evaluator_source_cli_uses_agent_markdown_model_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "answers.jsonl"
    _write_answer_source(input_path)
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text(
        "---\nname: judge\nmodel_profile: judge\n---\nJudge.\n",
        encoding="utf-8",
    )
    captured = {}

    monkeypatch.setattr(
        "aworld_cli.core.model_profiles.resolve_model_profile",
        lambda profile: ModelConfig(
            llm_provider="openai",
            llm_model_name=f"{profile}-model",
            llm_api_key="profile-key",
        ),
    )

    async def fake_run_evaluation_flow(flow):
        captured["backend"] = flow.suite.judge_backend
        return {
            "report_version": 1,
            "suite_id": "answer-source-evaluator",
            "summary": {"answer-source-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    run_evaluator_source_cli(
        input=str(input_path),
        kind="answer",
        judge_agent=str(judge_agent),
    )

    assert captured["backend"].model_config.llm_model_name == "judge-model"


def test_source_file_judge_agent_uses_direct_instruction_backend(tmp_path: Path) -> None:
    input_path = tmp_path / "answers.jsonl"
    _write_answer_source(input_path)
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text(
        "---\nname: judge\n---\nReturn JSON only.\n",
        encoding="utf-8",
    )

    suite = _build_source_suite(
        kind="answer",
        input_path=input_path,
        judge_agent_path=judge_agent,
        task_id=None,
        id_field="id",
        task_field="input",
        answer_field="answer",
        out_dir=str(tmp_path),
    )

    assert suite.judge_backend.backend_id == "source-agent-md"
    assert suite.judge_backend.executor is None
    assert "Return JSON only." in suite.judge_backend.system_prompt
    assert "Agent loaded from" not in suite.judge_backend.system_prompt


def test_run_evaluator_source_cli_supports_cli_judge_agent_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "answers.jsonl"
    _write_answer_source(input_path)
    captured = {}

    class FakeExecutor:
        async def chat(self, prompt):
            captured["prompt"] = prompt
            return '{"score": 91, "verdict": "Pass", "veto_triggered": false}'

    async def fake_load_cli_agent_executor(agent_name):
        captured["agent_name"] = agent_name
        return FakeExecutor()

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._load_cli_agent_executor",
        fake_load_cli_agent_executor,
    )

    async def fake_run_evaluation_flow(flow):
        captured["flow"] = flow
        execution = await flow.suite.judge_backend.execute(
            flow.suite.cases[0].input,
            {"answer": "existing"},
            flow.suite,
        )
        return {
            "report_version": 1,
            "suite_id": "answer-source-evaluator",
            "judge_backend": {"backend_id": execution.backend_id},
            "summary": {"answer-source-evaluator": {"score": {"mean": execution.payload["score"]}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": execution.payload["score"]},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_source_cli(
        input=str(input_path),
        kind="answer",
        judge_agent_name="JudgeTeam",
        output=str(tmp_path / "report.json"),
    )

    assert captured["agent_name"] == "JudgeTeam"
    assert captured["flow"].suite.judge_backend.backend_id == "source-agent:JudgeTeam"
    assert report["judge_backend"]["backend_id"] == "source-agent:JudgeTeam"
    assert report["source_selection"]["judge_agent_name"] == "JudgeTeam"
    assert report["source_selection"]["judge_agent"] is None


def test_run_evaluator_source_cli_applies_model_profile_to_cli_judge_agent_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "answers.jsonl"
    _write_answer_source(input_path)
    captured = {}
    judge_conf = SimpleNamespace(llm_config=None)
    judge_agent = SimpleNamespace(conf=judge_conf)
    judge_swarm = SimpleNamespace(_communicate_agent=judge_agent)

    class FakeExecutor:
        swarm = judge_swarm

    async def fake_load_cli_agent_executor(agent_name):
        captured["agent_name"] = agent_name
        return FakeExecutor()

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._load_cli_agent_executor",
        fake_load_cli_agent_executor,
    )
    monkeypatch.setattr(
        "aworld_cli.core.model_profiles.resolve_model_profile",
        lambda profile: ModelConfig(
            llm_provider="anthropic",
            llm_model_name=f"{profile}-model",
            llm_api_key="profile-key",
        ),
    )

    async def fake_run_evaluation_flow(flow):
        captured["flow"] = flow
        await flow.suite.judge_backend.execute(
            flow.suite.cases[0].input,
            {"answer": "existing"},
            flow.suite,
        )
        return {
            "report_version": 1,
            "suite_id": "answer-source-evaluator",
            "judge_backend": {"backend_id": flow.suite.judge_backend.backend_id},
            "summary": {"answer-source-evaluator": {"score": {"mean": 91}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 91},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    async def fake_runner_run(**kwargs):
        return SimpleNamespace(answer='{"score": 91, "verdict": "Pass", "veto_triggered": false}')

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)
    monkeypatch.setattr("aworld_cli.evaluator_runtime.Runners.run", fake_runner_run)

    report = run_evaluator_source_cli(
        input=str(input_path),
        kind="answer",
        judge_agent_name="JudgeTeam",
        judge_model_profile="judge",
        output=str(tmp_path / "report.json"),
    )

    assert captured["agent_name"] == "JudgeTeam"
    assert judge_conf.llm_config.llm_provider == "anthropic"
    assert judge_conf.llm_config.llm_model_name == "judge-model"
    assert judge_conf.llm_config.llm_api_key == "profile-key"
    assert report["source_selection"]["judge_model_profile"] == "judge"


def test_run_evaluator_source_cli_supports_judge_backend_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "answers.jsonl"
    _write_answer_source(input_path)
    module_path = tmp_path / "custom_judge.py"
    module_path.write_text(
        "\n".join(
            [
                "from aworld.evaluations.substrate import CallableJudgeBackend",
                "",
                "async def judge(case_input, target):",
                "    return {'score': 82, 'verdict': 'Pass', 'veto_triggered': False}",
                "",
                "def build_backend():",
                "    return CallableJudgeBackend(backend_id='custom-backend', judge=judge)",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    captured = {}

    async def fake_run_evaluation_flow(flow):
        captured["flow"] = flow
        execution = await flow.suite.judge_backend.execute(
            flow.suite.cases[0].input,
            {"answer": "existing"},
            flow.suite,
        )
        return {
            "report_version": 1,
            "suite_id": "answer-source-evaluator",
            "judge_backend": {"backend_id": execution.backend_id},
            "summary": {"answer-source-evaluator": {"score": {"mean": execution.payload["score"]}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": execution.payload["score"]},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_source_cli(
        input=str(input_path),
        kind="answer",
        judge_backend_ref="custom_judge:build_backend",
        output=str(tmp_path / "report.json"),
    )

    assert captured["flow"].suite.judge_backend.backend_id == "custom-backend"
    assert report["judge_backend"]["backend_id"] == "custom-backend"
    assert report["source_selection"]["judge_backend_ref"] == "custom_judge:build_backend"


def test_run_evaluator_source_cli_rejects_missing_judge_selector(tmp_path: Path) -> None:
    input_path = tmp_path / "answers.jsonl"
    _write_answer_source(input_path)

    with pytest.raises(ValueError, match="exactly one judge selector"):
        run_evaluator_source_cli(
            input=str(input_path),
            kind="answer",
            output=str(tmp_path / "report.json"),
        )


def test_run_evaluator_source_cli_rejects_multiple_judge_selectors(tmp_path: Path) -> None:
    input_path = tmp_path / "answers.jsonl"
    _write_answer_source(input_path)
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one judge selector"):
        run_evaluator_source_cli(
            input=str(input_path),
            kind="answer",
            judge_agent=str(judge_agent),
            judge_agent_name="JudgeTeam",
            output=str(tmp_path / "report.json"),
        )


def test_run_evaluator_source_cli_builds_task_flow_with_default_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "tasks.jsonl"
    input_path.write_text('{"id":"case-1","input":"question"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")
    captured = {}

    class FakeHarness:
        pass

    def fake_build_cli_agent_runtime_harness(*, agent_name):
        captured["agent_name"] = agent_name
        return FakeHarness()

    async def fake_run_evaluation_flow(flow):
        captured["flow"] = flow
        return {
            "report_version": 1,
            "suite_id": "task-source-evaluator",
            "judge_backend": {"backend_id": "source-agent-md"},
            "summary": {"task-source-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._build_cli_agent_runtime_harness",
        fake_build_cli_agent_runtime_harness,
    )
    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_source_cli(
        input=str(input_path),
        kind="task",
        judge_agent=str(judge_agent),
        output=str(tmp_path / "report.json"),
    )

    flow = captured["flow"]
    assert captured["agent_name"] == "Aworld"
    assert flow.target["source_kind"] == "task"
    assert flow.target["agent"] == "Aworld"
    assert flow.suite.cases[0].case_id == "case-1"
    assert flow.suite.cases[0].input == {"input": "question"}
    assert flow.suite.runtime_harness is not None
    assert report["source_selection"]["kind"] == "task"
    assert report["source_selection"]["agent"] == "Aworld"
    assert report["automation"]["source_kind"] == "task"


def test_task_source_gate_consumes_answer_veto_signal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "tasks.jsonl"
    input_path.write_text('{"id":"case-1","input":"question"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")

    class FakeHarness:
        pass

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._build_cli_agent_runtime_harness",
        lambda *, agent_name: FakeHarness(),
    )

    suite = _build_source_suite(
        kind="task",
        input_path=input_path,
        judge_agent_path=judge_agent,
        task_id=None,
        id_field="id",
        task_field="input",
        answer_field="answer",
        out_dir=str(tmp_path),
    )

    payload = suite.judge_schema.validate_payload(
        {"score": 95.0, "verdict": "Excellent", "veto_triggered": True}
    )
    assert payload["veto_triggered"] is True
    pass_conditions = suite.gate_policy.normalized_conditions()[0]
    assert any(
        condition.metric_name == "veto_triggered"
        and condition.op == "=="
        and condition.threshold is False
        for condition in pass_conditions
    )
    decision = suite.gate_policy.evaluate({"score": 95.0, "veto_triggered": True})
    assert decision.status == "fail"
    assert any(condition["metric_name"] == "veto_triggered" for condition in decision.failed_conditions)


def test_run_evaluator_source_cli_builds_generated_trajectory_flow_with_default_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "tasks.jsonl"
    input_path.write_text('{"id":"case-1","input":"question"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")
    captured = {}

    class FakeHarness:
        pass

    def fake_build_cli_agent_runtime_harness(*, agent_name):
        captured["agent_name"] = agent_name
        return FakeHarness()

    async def fake_run_evaluation_flow(flow):
        captured["flow"] = flow
        return {
            "report_version": 1,
            "suite_id": "trajectory-source-evaluator",
            "judge_backend": {"backend_id": "trajectory-evaluator-agent-md"},
            "summary": {"trajectory-source-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._build_cli_agent_runtime_harness",
        fake_build_cli_agent_runtime_harness,
    )
    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_source_cli(
        input=str(input_path),
        kind="trajectory",
        judge_agent=str(judge_agent),
        output=str(tmp_path / "report.json"),
    )

    flow = captured["flow"]
    assert captured["agent_name"] == "Aworld"
    assert flow.target["source_kind"] == "trajectory"
    assert flow.target["agent"] == "Aworld"
    assert flow.suite.cases[0].case_id == "case-1"
    assert flow.suite.cases[0].input == {"input": "question"}
    assert report["source_selection"]["kind"] == "trajectory"
    assert report["source_selection"]["agent"] == "Aworld"


@pytest.mark.asyncio
async def test_cli_agent_runtime_harness_returns_rollout_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeExecutor:
        async def chat(self, query):
            return f"answer for {query}"

    async def fake_load_cli_agent_executor(agent_name):
        assert agent_name == "Aworld"
        return FakeExecutor()

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._load_cli_agent_executor",
        fake_load_cli_agent_executor,
    )

    case = SimpleNamespace(
        case_id="case-1",
        input={"input": "question"},
        metadata={
            "source_record": {
                "metadata": {"source_kind": "task", "source_path": "tasks.jsonl"},
            },
        },
    )
    state = await _CliAgentRuntimeHarness(agent_name="Aworld").run_rollout(
        case=case,
        target={"source_kind": "task"},
    )

    assert state.status == "success"
    assert state.answer == "answer for question"
    assert state.outcome["has_answer"] is True
    assert state.metadata["agent"] == "Aworld"
    assert state.metadata["source_kind"] == "task"
    assert state.standard_metrics["n_turns"] == 2


@pytest.mark.asyncio
async def test_cli_agent_runtime_harness_prefers_swarm_task_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSwarm:
        pass

    class FakeExecutor:
        swarm = FakeSwarm()

        async def chat(self, query):
            raise AssertionError("chat fallback should not be used for local swarm executors")

    async def fake_load_cli_agent_executor(agent_name):
        return FakeExecutor()

    async def fake_run(*, input, swarm):
        assert input == "question"
        assert isinstance(swarm, FakeSwarm)
        return {
            "answer": "answer with tools",
            "trajectory": [{"tool_calls": [{"name": "search"}]}],
            "usage": {"total_tokens": 12},
        }

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._load_cli_agent_executor",
        fake_load_cli_agent_executor,
    )
    monkeypatch.setattr("aworld_cli.evaluator_runtime.Runners.run", fake_run)

    case = SimpleNamespace(case_id="case-1", input={"input": "question"}, metadata={})
    state = await _CliAgentRuntimeHarness(agent_name="Aworld").run_rollout(
        case=case,
        target={"source_kind": "task"},
    )

    assert state.answer == "answer with tools"
    assert state.tool_calls == [{"name": "search"}]
    assert state.trajectory == [{"tool_calls": [{"name": "search"}]}]
    assert state.standard_metrics["n_tool_calls"] == 1
    assert state.standard_metrics["n_tokens"] == 12


def test_source_prompt_uses_zero_to_hundred_score_contract() -> None:
    prompt = _build_source_prompt(
        {"input": "question"},
        {"answer": "existing"},
        suite=None,
    )

    payload = json.loads(prompt)
    assert payload["required_output_schema"]["score"] == "number, weighted score from 0 to 100"
    assert payload["required_output_schema"]["veto_triggered"] == "boolean, true only for one-vote veto failures"


def test_run_evaluator_source_cli_rejects_unsupported_source_kind(tmp_path: Path) -> None:
    input_path = tmp_path / "tasks.jsonl"
    input_path.write_text('{"id":"case-1","input":"question"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported source kind"):
        run_evaluator_source_cli(
            input=str(input_path),
            kind="task-only",
            judge_agent=str(judge_agent),
        )


def test_trajectory_source_gate_consumes_veto_signal(tmp_path: Path) -> None:
    task_id = "task-with-veto"
    trajectory = [
        {
            "state": {"input": {"content": "question"}, "messages": []},
            "meta": {"step": 1},
            "action": {"content": "final", "is_agent_finished": "True"},
        }
    ]
    input_path = tmp_path / "trajectory.log"
    input_path.write_text(
        repr({"task_id": task_id, "is_sub_task": False, "trajectory": json.dumps(trajectory)}) + "\n",
        encoding="utf-8",
    )
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")

    suite = _build_source_suite(
        kind="trajectory",
        input_path=input_path,
        judge_agent_path=judge_agent,
        task_id=task_id,
        id_field="id",
        task_field="input",
        answer_field="answer",
        out_dir=str(tmp_path),
    )

    pass_conditions = suite.gate_policy.normalized_conditions()[0]
    assert any(
        condition.metric_name == "veto_triggered"
        and condition.op == "=="
        and condition.threshold is False
        for condition in pass_conditions
    )
    decision = suite.gate_policy.evaluate(
        {
            "score": 95.0,
            "A1_groundedness": 5,
            "has_evidence": 1.0,
            "agent_finished": 1.0,
            "veto_triggered": True,
        }
    )
    assert decision.status == "fail"
    assert any(condition["metric_name"] == "veto_triggered" for condition in decision.failed_conditions)


def test_trajectory_source_judge_system_prompt_prefers_artifact_backed_contract(
    tmp_path: Path,
) -> None:
    task_id = "task-contract"
    trajectory = [
        {
            "state": {"input": {"content": "question"}, "messages": []},
            "meta": {"step": 1},
            "action": {"content": "final", "is_agent_finished": "True"},
        }
    ]
    input_path = tmp_path / "trajectory.log"
    input_path.write_text(
        repr({"task_id": task_id, "is_sub_task": False, "trajectory": json.dumps(trajectory)}) + "\n",
        encoding="utf-8",
    )
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text(
        "---\nname: judge\n---\nParse TRAJECTORY_LOG yourself before scoring.\n",
        encoding="utf-8",
    )

    suite = _build_source_suite(
        kind="trajectory",
        input_path=input_path,
        judge_agent_path=judge_agent,
        task_id=task_id,
        id_field="id",
        task_field="input",
        answer_field="answer",
        out_dir=str(tmp_path),
    )

    system_prompt = suite.judge_backend.system_prompt
    assert system_prompt.startswith("AWorld trajectory evaluator runtime contract:")
    assert "Prefer evidence_digest over artifact_backed_evidence" in system_prompt
    assert "artifact_read_requests" in system_prompt
    assert "Parse TRAJECTORY_LOG yourself before scoring" in system_prompt


def test_aworld_trajectory_log_without_task_id_builds_task_execution_suite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "tasks.jsonl"
    input_path.write_text('{"id":"case-1","input":"question"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")
    captured = {}

    class FakeHarness:
        pass

    def fake_build_cli_agent_runtime_harness(*, agent_name):
        captured["agent_name"] = agent_name
        return FakeHarness()

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._build_cli_agent_runtime_harness",
        fake_build_cli_agent_runtime_harness,
    )

    suite = _build_source_suite(
        kind="trajectory",
        input_path=input_path,
        judge_agent_path=judge_agent,
        task_id=None,
        id_field="id",
        task_field="input",
        answer_field="answer",
        out_dir=str(tmp_path),
    )

    assert captured["agent_name"] == "Aworld"
    assert suite.suite_id == "trajectory-source-evaluator"
    assert suite.cases[0].case_id == "case-1"
    assert suite.cases[0].input == {"input": "question"}
    assert suite.runtime_harness is not None
    assert suite.judge_backend.backend_id == "trajectory-evaluator-agent-md"
    pass_conditions = suite.gate_policy.normalized_conditions()[0]
    assert any(condition.metric_name == "A1_groundedness" for condition in pass_conditions)
    assert any(condition.metric_name == "veto_triggered" for condition in pass_conditions)


def test_trajectory_log_without_task_id_builds_replay_suite_for_all_tasks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "trajectory.log"
    trajectory = [
        {
            "state": {"input": {"content": "question"}, "messages": []},
            "meta": {"step": 1},
            "action": {"content": "final", "is_agent_finished": "True"},
        }
    ]
    input_path.write_text(
        "\n".join(
            [
                repr({"task_id": "task-1", "is_sub_task": False, "trajectory": json.dumps(trajectory)}),
                repr({"task_id": "task-2", "is_sub_task": False, "trajectory": json.dumps(trajectory)}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")

    def fake_build_cli_agent_runtime_harness(*, agent_name):
        raise AssertionError("trajectory log replay must not execute the main agent")

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._build_cli_agent_runtime_harness",
        fake_build_cli_agent_runtime_harness,
    )

    suite = _build_source_suite(
        kind="trajectory",
        input_path=input_path,
        judge_agent_path=judge_agent,
        task_id=None,
        id_field="id",
        task_field="input",
        answer_field="answer",
        out_dir=str(tmp_path),
    )

    assert suite.suite_id == "trajectory-source-evaluator"
    assert [case.case_id for case in suite.cases] == ["task-1", "task-2"]
    assert suite.runtime_harness is not None


def test_trajectory_prompt_can_use_generated_runtime_trajectory() -> None:
    prompt = json.loads(
        _build_trajectory_prompt(
            {"input": "question", "_case_metadata": {}},
            {
                "case_id": "case-1",
                "answer": "final answer",
                "trajectory": [
                    {
                        "state": {
                            "input": {"content": "question"},
                            "messages": [{"role": "tool", "content": "evidence"}],
                        },
                        "meta": {"step": 1, "agent_id": "Aworld"},
                        "action": {
                            "content": "final answer",
                            "is_agent_finished": "True",
                            "tool_calls": [{"function": {"name": "search", "arguments": "{}"}}],
                        },
                    }
                ],
            },
            suite=None,
        )
    )

    extracted = prompt["extracted_trajectory"]
    assert extracted["task_id"] == "case-1"
    assert extracted["question"] == "question"
    assert extracted["final_answer"] == "final answer"
    assert extracted["evidence"][0]["content"] == "evidence"
    assert extracted["steps"][0]["tool_calls"] == [{"name": "search", "arguments": "{}"}]
    runtime_context = prompt["runtime_context"]
    assert runtime_context["trajectory_log_path"] == ""
    assert runtime_context["task_id"] == "case-1"
    assert runtime_context["TRAJECTORY_LOG"] == ""
    assert runtime_context["TASK_ID"] == "case-1"
    assert runtime_context["OUT_DIR"] == ""
    contract = prompt["evaluation_runtime_contract"]
    assert contract["inputs_are_complete"] is True
    assert contract["primary_evaluation_input"] == "evidence_digest"
    assert contract["secondary_evaluation_input"] == "artifact_backed_evidence"
    assert contract["bounded_prompt_input"] == "extracted_trajectory"
    assert contract["do_not_request_missing_parameters"] is True
    assert contract["output_format"] == "single_json_object"
    assert "Do not ask the user for TRAJECTORY_LOG" in prompt["instruction"]
    assert "Return only one compact JSON object" in prompt["instruction"]
    assert "Do not include analysis" in prompt["instruction"]
    assert "Do not include markdown" in prompt["instruction"]
    assert prompt["evidence_digest"]["mode"] == "judge_ready_evidence_digest"
    assert prompt["evidence_digest"]["entries"][0]["evidence"]["excerpt"] == "evidence"
    read_policy = prompt["artifact_backed_evidence"]["read_policy"]
    assert read_policy["projection_strategy"] == "incremental_non_overlapping_ranges"
    assert read_policy["max_rounds"] == 2
    assert read_policy["max_total_chars"] == 80000
    constraint_schema = prompt["required_output_schema"][
        "evidence_repair_constraints"
    ][0]
    assert "projection_compacted" in constraint_schema["failure_mode"]
    assert "support_incomplete" in constraint_schema["failure_mode"]


def test_build_trajectory_prompt_includes_runtime_context_from_source_target() -> None:
    prompt = json.loads(
        _build_trajectory_prompt(
            case_input={"task_id": "task-1", "trajectory_log": "/tmp/trajectory.log"},
            target={
                "target_path": "/tmp/trajectory.log",
                "source_out_dir": "/tmp/extracted",
                "report_output_path": "/tmp/report.json",
                "artifacts": {
                    "outcome": {
                        "extracted_path": None,
                    }
                },
                "trajectory": [
                    {
                        "state": {"input": {"content": "question"}, "messages": []},
                        "meta": {"step": 1, "agent_id": "Aworld"},
                        "action": {"content": "answer", "is_agent_finished": "True"},
                    }
                ],
            },
            suite=None,
        )
    )

    runtime_context = prompt["runtime_context"]
    assert runtime_context == {
        "trajectory_log_path": "/tmp/trajectory.log",
        "task_id": "task-1",
        "out_dir": "/tmp/extracted",
        "report_output_path": "/tmp/report.json",
        "TRAJECTORY_LOG": "/tmp/trajectory.log",
        "TASK_ID": "task-1",
        "OUT_DIR": "/tmp/extracted",
    }


def test_trajectory_prompt_includes_canonical_evidence_bundle(tmp_path: Path) -> None:
    bundle_path = tmp_path / "evidence_bundle.json"
    _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "source-1",
                "artifact_path": str(tmp_path / "source.txt"),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {
                    "bounded_excerpt": "short verified evidence",
                },
            }
        ],
    )

    prompt = json.loads(
        _build_trajectory_prompt(
            {"input": "question"},
            {
                "case_id": "case-1",
                "answer": "answer",
                "trajectory": [
                    {
                        "state": {"input": {"content": "question"}, "messages": []},
                        "meta": {"step": 1, "agent_id": "Aworld"},
                        "action": {"content": "answer", "is_agent_finished": "True"},
                    }
                ],
                "artifacts": {
                    "outcome": {
                        "extracted_path": None,
                    }
                },
                "evidence_bundle_path": str(bundle_path),
            },
            suite=None,
        )
    )

    bundle = prompt["extracted_trajectory"]["evidence_bundle"]
    assert bundle["valid"] is True
    assert bundle["entry_count"] == 1
    assert bundle["entries"][0]["source_id"] == "source-1"
    assert bundle["entries"][0]["bounded_evidence"]["bounded_excerpt"] == (
        "short verified evidence"
    )
    assert prompt["evidence_summary"]["canonical_bundle_entry_count"] == 1
    assert prompt["evaluation_runtime_contract"]["primary_evaluation_input"] == "evidence_digest"
    assert prompt["evidence_digest"]["canonical_bundle_valid"] is True
    assert prompt["evidence_digest"]["entries"][0]["evidence"]["bounded_excerpt"] == (
        "short verified evidence"
    )


def test_trajectory_prompt_preserves_non_file_evidence_metadata_in_digest(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "evidence_bundle.json"
    _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "scheduled_notification",
                "evidence_type": "metadata",
                "extraction_method": "scheduler_response",
                "metadata": {
                    "operation": "schedule_notification",
                    "reference_id": "job-123",
                    "status": "scheduled",
                },
                "bounded_evidence": {
                    "bounded_excerpt": "Notification job job-123 was scheduled."
                },
            }
        ],
    )

    prompt = json.loads(
        _build_trajectory_prompt(
            {"input": "schedule the report"},
            {
                "case_id": "case-1",
                "answer": "scheduled",
                "trajectory": [],
                "artifacts": {"outcome": {"extracted_path": None}},
                "evidence_bundle_path": str(bundle_path),
            },
            suite=None,
        )
    )

    digest_entry = prompt["evidence_digest"]["entries"][0]
    assert digest_entry["evidence_type"] == "metadata"
    assert digest_entry["metadata"] == {
        "operation": "schedule_notification",
        "reference_id": "job-123",
        "status": "scheduled",
    }
    assert digest_entry["evidence"]["bounded_excerpt"] == (
        "Notification job job-123 was scheduled."
    )
    assert "artifact_path" not in digest_entry


def test_trajectory_prompt_uses_bundle_first_compaction_for_large_replay_payload(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "evidence_manifest.jsonl"
    manifest_payload = (
        json.dumps(
            {
                "source_id": "source-1",
                "artifact_path": str(tmp_path / "source.txt"),
                "extraction_method": "bounded_extract",
                "bounded_excerpt": "compact verified evidence",
            }
        )
        + "\n"
    ).encode("utf-8")
    manifest_path.write_bytes(manifest_payload)
    manifest_fingerprint = "sha256:" + hashlib.sha256(manifest_payload).hexdigest()
    bundle_path = tmp_path / "evidence_bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "format": "aworld.self_evolve.evidence_bundle",
                "version": 1,
                "valid": True,
                "manifest_path": str(manifest_path),
                "manifest": {
                    "path": str(manifest_path),
                    "present": True,
                    "readable": True,
                    "valid": True,
                    "entry_count": 1,
                    "invalid_entry_count": 0,
                    "size_bytes": len(manifest_payload),
                    "fingerprint": manifest_fingerprint,
                },
                "entries": [
                    {
                        "source_id": "source-1",
                        "artifact_path": str(tmp_path / "source.txt"),
                        "extraction_method": "bounded_extract",
                        "bounded_evidence": {
                            "bounded_excerpt": "compact verified evidence",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    extracted_path = tmp_path / "extracted.json"
    extracted_path.write_text(
        json.dumps(
            {
                "task_id": "case-1",
                "question": (
                    "summarize the source"
                    "\n\nSelf-evolve replay evidence requirements:\n"
                    + ("internal replay instruction " * 200)
                ),
                "system_prompt_excerpt": "system instructions " * 1000,
                "steps": [
                    {
                        "step": index,
                        "agent_id": "Aworld",
                        "tool_calls": [
                            {
                                "name": "reader",
                                "args": {"content": "large argument " * 1000},
                            }
                        ],
                        "assistant_content": "assistant reasoning " * 1000,
                        "is_agent_finished": index == 3,
                    }
                    for index in range(1, 4)
                ],
                "final_answer": "answer",
                "evidence": [
                    {
                        "source": "state.messages",
                        "content": "raw evidence " * 5000,
                        "original_length": 65000,
                        "truncated": True,
                    }
                    for _ in range(8)
                ],
                "evidence_bundle_path": str(bundle_path),
            }
        ),
        encoding="utf-8",
    )

    prompt = json.loads(
        _build_trajectory_prompt(
            {"input": "summarize the source"},
            {
                "case_id": "case-1",
                "answer": "answer",
                "artifacts": {"outcome": {"extracted_path": str(extracted_path)}},
            },
            suite=None,
        )
    )

    trajectory = prompt["extracted_trajectory"]
    evidence = trajectory["evidence"]
    assert prompt["evidence_summary"]["bundle_first"] is True
    assert prompt["evidence_summary"]["raw_evidence_content_suppressed"] is True
    assert trajectory["evidence_bundle"]["valid"] is True
    assert trajectory["evidence_bundle"]["manifest"]["fingerprint"] == (
        manifest_fingerprint
    )
    snapshot_manifest_path = trajectory["evidence_bundle"]["manifest"]["path"]
    assert snapshot_manifest_path != str(manifest_path)
    assert "Self-evolve replay evidence requirements" not in trajectory["question"]
    assert trajectory["system_prompt_excerpt"] == ""
    assert len(evidence) <= 3
    assert all("content" not in item for item in evidence)
    assert all(len(step.get("assistant_content", "")) <= 200 for step in trajectory["steps"])
    assert all(
        "args" not in call
        for step in trajectory["steps"]
        for call in step.get("tool_calls", [])
    )
    artifact_backed = prompt["artifact_backed_evidence"]
    assert artifact_backed["mode"] == "read_only_artifact_index"
    assert artifact_backed["prompt_payload_is_bounded"] is True
    assert artifact_backed["read_policy"]["external_network_allowed"] is False
    assert artifact_backed["read_policy"]["mutation_allowed"] is False
    assert artifact_backed["read_policy"]["max_rounds"] == 3
    assert artifact_backed["read_policy"]["max_total_chars"] == 120000
    assert {
        (artifact["kind"], artifact["path"])
        for artifact in artifact_backed["artifacts"]
    } >= {
        ("extracted_trajectory_json", str(extracted_path)),
        ("canonical_evidence_bundle", str(bundle_path)),
        ("evidence_manifest", snapshot_manifest_path),
        ("source_artifact", str(tmp_path / "source.txt")),
    }
    manifest_artifact = next(
        artifact
        for artifact in artifact_backed["artifacts"]
        if artifact["kind"] == "evidence_manifest"
    )
    assert manifest_artifact["available"] is True
    assert manifest_artifact["readable"] is True
    assert manifest_artifact["valid"] is True
    assert manifest_artifact["entry_count"] == 1
    assert manifest_artifact["fingerprint"] == manifest_fingerprint
    evidence_digest = prompt["evidence_digest"]
    assert prompt["evaluation_runtime_contract"]["primary_evaluation_input"] == "evidence_digest"
    assert evidence_digest["mode"] == "judge_ready_evidence_digest"
    assert evidence_digest["canonical_bundle_valid"] is True
    assert evidence_digest["entry_count"] == 1
    manifest_digest = dict(evidence_digest["manifest"])
    snapshot_session = manifest_digest.pop("snapshot_session")
    assert manifest_digest == {
        "path": snapshot_manifest_path,
        "source_path": str(manifest_path),
        "present": True,
        "readable": True,
        "regular_file": True,
        "valid": True,
        "entry_count": 1,
        "invalid_entry_count": 0,
        "size_bytes": len(manifest_payload),
        "fingerprint": manifest_fingerprint,
        "validation_errors": [],
        "content_addressed": True,
    }
    assert snapshot_session == artifact_backed["private_artifact_session"]
    assert snapshot_session["format"] == (
        "aworld.evaluation.private_artifact_session"
    )
    assert snapshot_session["version"] == 1
    assert len(snapshot_session["session_id"]) == 32
    assert evidence_digest["entries"] == [
        {
            "source_id": "source-1",
            "artifact_path": str(tmp_path / "source.txt"),
            "extraction_method": "bounded_extract",
            "evidence": {"bounded_excerpt": "compact verified evidence"},
        }
    ]
    assert evidence_digest["artifact_read_available"] is True
    assert "raw evidence" not in json.dumps(evidence_digest, ensure_ascii=False)
    assert prompt["evaluation_runtime_contract"]["may_use_read_only_artifact_access"] is True
    assert prompt["evaluation_runtime_contract"]["do_not_call_external_tools"] is True
    assert len(json.dumps(prompt, ensure_ascii=False)) < 30000


def test_load_prompt_evidence_bundle_revalidates_manifest_file(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.txt"
    source_path.write_text("verified evidence", encoding="utf-8")
    bundle_path = tmp_path / "evidence_bundle.json"
    manifest_path, manifest_fingerprint = _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "source-1",
                "artifact_path": str(source_path),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {"bounded_excerpt": "verified evidence"},
            }
        ],
    )

    bundle = _load_prompt_evidence_bundle(str(bundle_path))

    assert bundle["valid"] is True
    assert bundle["format"] == "aworld.self_evolve.evidence_bundle"
    assert bundle["version"] == 1
    assert bundle["manifest"]["present"] is True
    assert bundle["manifest"]["readable"] is True
    assert bundle["manifest"]["regular_file"] is True
    assert bundle["manifest"]["valid"] is True
    assert bundle["manifest"]["fingerprint"] == manifest_fingerprint
    assert bundle["manifest"]["validation_errors"] == []
    assert bundle["manifest"]["source_path"] == str(manifest_path)
    snapshot_path = Path(bundle["manifest"]["path"])
    assert snapshot_path != manifest_path
    assert snapshot_path.read_bytes() == manifest_path.read_bytes()
    assert snapshot_path.name == (
        "evidence-manifest-"
        + manifest_fingerprint.removeprefix("sha256:")
        + ".jsonl"
    )
    assert snapshot_path.stat().st_mode & 0o777 == 0o400
    assert snapshot_path.parent.stat().st_mode & 0o777 == 0o700
    assert bundle["manifest"]["content_addressed"] is True


def test_load_prompt_evidence_bundle_fails_closed_when_manifest_is_missing(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "evidence_bundle.json"
    manifest_path, _ = _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "source-1",
                "artifact_path": str(tmp_path / "source.txt"),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {"bounded_excerpt": "verified evidence"},
            }
        ],
    )
    manifest_path.unlink()

    bundle = _load_prompt_evidence_bundle(str(bundle_path))

    assert bundle["valid"] is False
    assert bundle["manifest"]["present"] is False
    assert bundle["manifest"]["readable"] is False
    assert bundle["manifest"]["valid"] is False
    assert "manifest_missing" in bundle["manifest"]["validation_errors"]


def test_load_prompt_evidence_bundle_fails_closed_when_manifest_is_replaced(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "evidence_bundle.json"
    manifest_path, expected_fingerprint = _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "source-1",
                "artifact_path": str(tmp_path / "source.txt"),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {"bounded_excerpt": "verified evidence"},
            }
        ],
    )
    manifest_path.write_text(
        json.dumps(
            {
                "source_id": "replacement",
                "artifact_path": str(tmp_path / "source.txt"),
                "extraction_method": "bounded_extract",
                "bounded_excerpt": "substituted evidence",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    bundle = _load_prompt_evidence_bundle(str(bundle_path))

    assert bundle["valid"] is False
    assert bundle["manifest"]["valid"] is False
    assert bundle["manifest"]["fingerprint"] != expected_fingerprint
    assert "manifest_size_mismatch" in bundle["manifest"]["validation_errors"]
    assert "manifest_fingerprint_mismatch" in bundle["manifest"]["validation_errors"]


def test_load_prompt_evidence_bundle_rejects_manifest_symlink(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "evidence_bundle.json"
    manifest_path, _ = _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "source-1",
                "artifact_path": str(tmp_path / "source.txt"),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {"bounded_excerpt": "verified evidence"},
            }
        ],
    )
    manifest_payload = manifest_path.read_bytes()
    manifest_target = tmp_path / "manifest-target.jsonl"
    manifest_target.write_bytes(manifest_payload)
    manifest_path.unlink()
    manifest_path.symlink_to(manifest_target)

    bundle = _load_prompt_evidence_bundle(str(bundle_path))

    assert bundle["valid"] is False
    assert bundle["manifest"]["present"] is True
    assert bundle["manifest"]["regular_file"] is False
    assert bundle["manifest"]["valid"] is False
    assert "manifest_not_regular_file" in bundle["manifest"]["validation_errors"]


def test_load_prompt_evidence_bundle_fails_closed_on_manifest_size_limit(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "evidence_bundle.json"
    manifest_path, _ = _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "source-1",
                "artifact_path": str(tmp_path / "source.txt"),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {"bounded_excerpt": "verified evidence"},
            }
        ],
    )
    oversized_payload = b"x" * (1024 * 1024 + 1)
    manifest_path.write_bytes(oversized_payload)
    persisted = json.loads(bundle_path.read_text(encoding="utf-8"))
    persisted["manifest"]["size_bytes"] = len(oversized_payload)
    persisted["manifest"]["fingerprint"] = (
        "sha256:" + hashlib.sha256(oversized_payload).hexdigest()
    )
    bundle_path.write_text(json.dumps(persisted), encoding="utf-8")

    bundle = _load_prompt_evidence_bundle(str(bundle_path))

    assert bundle["valid"] is False
    assert bundle["manifest"]["valid"] is False
    assert "manifest_size_limit_exceeded" in bundle["manifest"]["validation_errors"]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("format", "unknown.bundle", "bundle_format_mismatch"),
        ("version", 2, "bundle_version_mismatch"),
    ],
)
def test_load_prompt_evidence_bundle_validates_format_and_version(
    tmp_path: Path,
    field: str,
    value: object,
    error: str,
) -> None:
    bundle_path = tmp_path / "evidence_bundle.json"
    _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "source-1",
                "artifact_path": str(tmp_path / "source.txt"),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {"bounded_excerpt": "verified evidence"},
            }
        ],
    )
    persisted = json.loads(bundle_path.read_text(encoding="utf-8"))
    persisted[field] = value
    bundle_path.write_text(json.dumps(persisted), encoding="utf-8")

    bundle = _load_prompt_evidence_bundle(str(bundle_path))

    assert bundle["valid"] is False
    assert error in bundle["validation_errors"]


def test_load_prompt_evidence_bundle_fails_closed_on_entry_limit(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "evidence_bundle.json"
    entries = [
        {
            "source_id": f"source-{index}",
            "artifact_path": str(tmp_path / f"source-{index}.txt"),
            "extraction_method": "bounded_extract",
            "bounded_evidence": {"bounded_excerpt": f"evidence {index}"},
        }
        for index in range(257)
    ]
    _write_verified_evidence_bundle(bundle_path, entries)

    bundle = _load_prompt_evidence_bundle(str(bundle_path))

    assert bundle["valid"] is False
    assert bundle["entry_count"] == 256
    assert "bundle_entry_limit_exceeded" in bundle["validation_errors"]
    assert "manifest_entry_limit_exceeded" in bundle["manifest"]["validation_errors"]


def test_load_prompt_evidence_bundle_validates_bundle_and_manifest_entry_schema(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "evidence_bundle.json"
    manifest_path, _ = _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "source-1",
                "artifact_path": str(tmp_path / "source.txt"),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {"bounded_excerpt": "verified evidence"},
            }
        ],
    )
    invalid_manifest = b'{"source_id":"","extraction_method":"bounded_extract"}\n'
    manifest_path.write_bytes(invalid_manifest)
    persisted = json.loads(bundle_path.read_text(encoding="utf-8"))
    persisted["entries"].append("not-an-object")
    persisted["manifest"]["size_bytes"] = len(invalid_manifest)
    persisted["manifest"]["fingerprint"] = (
        "sha256:" + hashlib.sha256(invalid_manifest).hexdigest()
    )
    bundle_path.write_text(json.dumps(persisted), encoding="utf-8")

    bundle = _load_prompt_evidence_bundle(str(bundle_path))

    assert bundle["valid"] is False
    assert "bundle_entry_not_object" in bundle["validation_errors"]
    assert any(
        error.startswith("manifest_entry_schema_invalid:")
        for error in bundle["manifest"]["validation_errors"]
    )


@pytest.mark.parametrize(
    "mutation",
    ["artifact_path", "metadata", "bounded_evidence"],
)
def test_load_prompt_evidence_bundle_binds_complete_canonical_manifest_record(
    tmp_path: Path,
    mutation: str,
) -> None:
    bundle_path = tmp_path / "evidence_bundle.json"
    source_path = tmp_path / "source.txt"
    source_path.write_text("verified source", encoding="utf-8")
    replacement_path = tmp_path / "replacement.txt"
    replacement_path.write_text("replacement source", encoding="utf-8")
    entries = [
        {
            "source_id": "artifact-source",
            "artifact_path": str(source_path),
            "extraction_method": "bounded_extract",
            "bounded_evidence": {"bounded_excerpt": "verified source"},
        },
        {
            "source_id": "metadata-source",
            "evidence_type": "metadata",
            "extraction_method": "structured_result",
            "metadata": {"status": "verified", "count": 3},
            "bounded_evidence": {"bounded_excerpt": "verified metadata"},
        },
    ]
    _write_verified_evidence_bundle(bundle_path, entries)
    persisted = json.loads(bundle_path.read_text(encoding="utf-8"))
    if mutation == "artifact_path":
        persisted["entries"][0]["artifact_path"] = str(replacement_path)
    elif mutation == "metadata":
        persisted["entries"][1]["metadata"]["status"] = "forged"
    else:
        persisted["entries"][0]["bounded_evidence"]["bounded_excerpt"] = (
            "forged excerpt"
        )
    bundle_path.write_text(json.dumps(persisted), encoding="utf-8")

    bundle = _load_prompt_evidence_bundle(str(bundle_path))

    assert bundle["valid"] is False
    assert any(
        error.startswith("manifest_bundle_entry_content_mismatch:")
        for error in bundle["manifest"]["validation_errors"]
    )


def test_load_prompt_evidence_bundle_accepts_framework_archived_artifact_path(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "artifacts"
    bundle_dir.mkdir()
    workspace_source = tmp_path / "workspace" / "data" / "source.txt"
    workspace_source.parent.mkdir(parents=True)
    workspace_source.write_text("archived verified source", encoding="utf-8")
    archive_dir = bundle_dir / "workspace_evidence"
    archive_dir.mkdir()
    archive_prefix = hashlib.sha256(
        str(workspace_source.resolve()).encode("utf-8")
    ).hexdigest()[:12]
    archived_path = archive_dir / (
        f"{archive_prefix}__data__source.txt"
    )
    archived_path.write_text("archived verified source", encoding="utf-8")
    bundle_path = bundle_dir / "evidence_bundle.json"
    _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "archived-source",
                "artifact_path": str(archived_path),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {
                    "bounded_excerpt": "archived verified source"
                },
            }
        ],
        manifest_entries=[
            {
                "source_id": "archived-source",
                "artifact_path": str(workspace_source),
                "extraction_method": "bounded_extract",
                "bounded_excerpt": "archived verified source",
            }
        ],
    )

    bundle = _load_prompt_evidence_bundle(str(bundle_path))

    assert bundle["valid"] is True
    assert bundle["entries"][0]["artifact_path"] == str(archived_path)


def test_load_prompt_evidence_bundle_detects_manifest_toctou(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "evidence_bundle.json"
    manifest_path, _ = _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "source-1",
                "artifact_path": str(tmp_path / "source.txt"),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {"bounded_excerpt": "verified evidence"},
            }
        ],
    )
    real_lstat = os.lstat

    def replaced_lstat(path: object):
        stat_result = real_lstat(path)
        if Path(path) != manifest_path:
            return stat_result
        return SimpleNamespace(
            st_mode=stat_result.st_mode,
            st_dev=stat_result.st_dev,
            st_ino=stat_result.st_ino + 1,
            st_size=stat_result.st_size,
            st_mtime_ns=stat_result.st_mtime_ns,
        )

    monkeypatch.setattr(evaluator_runtime_module.os, "lstat", replaced_lstat)

    bundle = _load_prompt_evidence_bundle(str(bundle_path))

    assert bundle["valid"] is False
    assert bundle["manifest"]["valid"] is False
    assert "manifest_path_changed_during_read" in (
        bundle["manifest"]["validation_errors"]
    )


def test_artifact_index_never_marks_unavailable_manifest_valid(
    tmp_path: Path,
) -> None:
    missing_manifest = (
        evaluator_runtime_module._VERIFIED_EVIDENCE_SNAPSHOT_ROOT
        / (
            "session-999999-1000000000000000-"
            + ("0" * 32)
        )
        / ("evidence-manifest-" + ("0" * 64) + ".jsonl")
    )
    index = _artifact_backed_evidence_index(
        runtime_context={},
        target={},
        extracted_path=None,
        extracted_payload={},
        evidence_bundle={
            "path": str(tmp_path / "evidence_bundle.json"),
            "valid": True,
            "entry_count": 1,
            "entries": [],
            "manifest": {
                "path": str(missing_manifest),
                "present": True,
                "readable": True,
                "regular_file": True,
                "valid": True,
                "entry_count": 1,
                "invalid_entry_count": 0,
                "size_bytes": 128,
                "fingerprint": "sha256:" + ("0" * 64),
                "content_addressed": True,
                "snapshot_session": {
                    "format": "aworld.evaluation.private_artifact_session",
                    "version": 1,
                    "session_id": "0" * 32,
                },
            },
        },
        evidence_summary={"canonical_bundle_valid": True},
    )

    manifest_artifact = next(
        artifact
        for artifact in index["artifacts"]
        if artifact["kind"] == "evidence_manifest"
    )
    assert manifest_artifact["available"] is False
    assert manifest_artifact["valid"] is False
    assert manifest_artifact["readable"] is False


@pytest.mark.parametrize("mutation", ["replace", "delete"])
def test_manifest_artifact_reads_verified_snapshot_after_source_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    source_path = tmp_path / "source.txt"
    source_path.write_text("verified source evidence", encoding="utf-8")
    bundle_path = tmp_path / "evidence_bundle.json"
    manifest_path, _ = _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "source-1",
                "artifact_path": str(source_path),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {
                    "bounded_excerpt": "verified source evidence"
                },
            }
        ],
    )
    verified_manifest_bytes = manifest_path.read_bytes()
    prompt_text = _build_trajectory_prompt(
        {"input": "question"},
        {
            "case_id": "case-1",
            "answer": "answer",
            "trajectory": [],
            "artifacts": {"outcome": {"extracted_path": None}},
            "evidence_bundle_path": str(bundle_path),
        },
        suite=None,
    )
    prompt = json.loads(prompt_text)
    manifest_artifact = next(
        artifact
        for artifact in prompt["artifact_backed_evidence"]["artifacts"]
        if artifact["kind"] == "evidence_manifest"
    )
    snapshot_path = manifest_artifact["path"]
    assert snapshot_path != str(manifest_path)
    if mutation == "replace":
        manifest_path.write_text(
            '{"source_id":"substituted","extraction_method":"forged"}\n',
            encoding="utf-8",
        )
    else:
        manifest_path.unlink()

    read_results = substrate_module._resolve_artifact_read_requests(
        prompt_text,
        [{"path": snapshot_path, "max_chars": 20_000}],
    )

    assert read_results[0]["status"] == "ok"
    assert read_results[0]["content"].encode("utf-8") == verified_manifest_bytes
    assert "substituted" not in read_results[0]["content"]


def test_manifest_artifact_read_fails_closed_when_snapshot_is_tampered(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "evidence_bundle.json"
    _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "snapshot-tamper",
                "artifact_path": str(tmp_path / "source.txt"),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {"bounded_excerpt": "verified evidence"},
            }
        ],
    )
    prompt_text = _build_trajectory_prompt(
        {"input": "question"},
        {
            "case_id": "case-1",
            "answer": "answer",
            "trajectory": [],
            "artifacts": {"outcome": {"extracted_path": None}},
            "evidence_bundle_path": str(bundle_path),
        },
        suite=None,
    )
    prompt = json.loads(prompt_text)
    manifest_artifact = next(
        artifact
        for artifact in prompt["artifact_backed_evidence"]["artifacts"]
        if artifact["kind"] == "evidence_manifest"
    )
    snapshot_path = Path(manifest_artifact["path"])
    snapshot_path.chmod(0o600)
    snapshot_path.write_text("substituted snapshot\n", encoding="utf-8")

    read_results = substrate_module._resolve_artifact_read_requests(
        prompt_text,
        [{"path": str(snapshot_path), "max_chars": 20_000}],
    )

    assert read_results[0]["status"] == "denied"
    assert read_results[0]["reason"] == "artifact_integrity_mismatch"
    assert "content" not in read_results[0]


def test_manifest_artifact_read_rejects_snapshot_symlink(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "evidence_bundle.json"
    manifest_path, _ = _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "snapshot-symlink",
                "artifact_path": str(tmp_path / "source.txt"),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {"bounded_excerpt": "verified evidence"},
            }
        ],
    )
    prompt_text = _build_trajectory_prompt(
        {"input": "question"},
        {
            "case_id": "case-1",
            "answer": "answer",
            "trajectory": [],
            "artifacts": {"outcome": {"extracted_path": None}},
            "evidence_bundle_path": str(bundle_path),
        },
        suite=None,
    )
    prompt = json.loads(prompt_text)
    snapshot_path = Path(
        next(
            artifact
            for artifact in prompt["artifact_backed_evidence"]["artifacts"]
            if artifact["kind"] == "evidence_manifest"
        )["path"]
    )
    snapshot_path.unlink()
    snapshot_path.symlink_to(manifest_path)

    read_results = substrate_module._resolve_artifact_read_requests(
        prompt_text,
        [{"path": str(snapshot_path), "max_chars": 20_000}],
    )

    assert read_results[0]["status"] == "denied"
    assert read_results[0]["reason"] == "artifact_not_regular_file"


def test_manifest_artifact_read_detects_snapshot_toctou(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "evidence_bundle.json"
    _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "snapshot-toctou",
                "artifact_path": str(tmp_path / "source.txt"),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {"bounded_excerpt": "verified evidence"},
            }
        ],
    )
    prompt_text = _build_trajectory_prompt(
        {"input": "question"},
        {
            "case_id": "case-1",
            "answer": "answer",
            "trajectory": [],
            "artifacts": {"outcome": {"extracted_path": None}},
            "evidence_bundle_path": str(bundle_path),
        },
        suite=None,
    )
    prompt = json.loads(prompt_text)
    snapshot_path = Path(
        next(
            artifact
            for artifact in prompt["artifact_backed_evidence"]["artifacts"]
            if artifact["kind"] == "evidence_manifest"
        )["path"]
    )
    real_lstat = os.lstat

    def changed_lstat(path: object):
        stat_result = real_lstat(path)
        if Path(path) != snapshot_path:
            return stat_result
        return SimpleNamespace(
            st_mode=stat_result.st_mode,
            st_dev=stat_result.st_dev,
            st_ino=stat_result.st_ino + 1,
            st_size=stat_result.st_size,
            st_mtime_ns=stat_result.st_mtime_ns,
        )

    monkeypatch.setattr(substrate_module.os, "lstat", changed_lstat)

    read_results = substrate_module._resolve_artifact_read_requests(
        prompt_text,
        [{"path": str(snapshot_path), "max_chars": 20_000}],
    )

    assert read_results[0]["status"] == "denied"
    assert read_results[0]["reason"] == "artifact_changed_during_read"


@pytest.mark.asyncio
async def test_evaluator_cleans_private_snapshot_session_after_judge(
    tmp_path: Path,
) -> None:
    observed_root: Path | None = None

    async def executor(prompt: str, system_prompt: str):
        nonlocal observed_root
        payload = json.loads(prompt)
        manifest_artifact = next(
            artifact
            for artifact in payload["artifact_backed_evidence"]["artifacts"]
            if artifact["kind"] == "evidence_manifest"
        )
        observed_root = Path(manifest_artifact["path"]).parent
        assert observed_root.exists()
        return {"score": 80.0, "verdict": "Pass"}

    backend = substrate_module.AgentJudgeBackend(
        backend_id="cleanup-test",
        system_prompt="judge",
        executor=executor,
        prompt_builder=_verified_evidence_prompt_builder(
            tmp_path / "normal-cleanup",
            source_id="normal-cleanup",
        ),
    )

    await backend.execute(
        {"input": "question"},
        {"answer": "answer"},
        substrate_module.EvalSuiteDef(suite_id="cleanup-suite"),
    )

    assert observed_root is not None
    assert observed_root.exists() is False
    assert (
        evaluator_runtime_module._VERIFIED_EVIDENCE_SNAPSHOT_ROOT.exists()
        is False
    )


@pytest.mark.asyncio
async def test_evaluator_cleans_private_snapshot_session_after_judge_exception(
    tmp_path: Path,
) -> None:
    observed_root: Path | None = None

    async def executor(prompt: str, system_prompt: str):
        nonlocal observed_root
        payload = json.loads(prompt)
        manifest_artifact = next(
            artifact
            for artifact in payload["artifact_backed_evidence"]["artifacts"]
            if artifact["kind"] == "evidence_manifest"
        )
        observed_root = Path(manifest_artifact["path"]).parent
        assert observed_root.exists()
        raise RuntimeError("judge failed")

    backend = substrate_module.AgentJudgeBackend(
        backend_id="cleanup-exception-test",
        system_prompt="judge",
        executor=executor,
        prompt_builder=_verified_evidence_prompt_builder(
            tmp_path / "exception-cleanup",
            source_id="exception-cleanup",
        ),
    )

    with pytest.raises(RuntimeError, match="judge failed"):
        await backend.execute(
            {"input": "question"},
            {"answer": "answer"},
            substrate_module.EvalSuiteDef(suite_id="cleanup-suite"),
        )

    assert observed_root is not None
    assert observed_root.exists() is False
    assert (
        evaluator_runtime_module._VERIFIED_EVIDENCE_SNAPSHOT_ROOT.exists()
        is False
    )


@pytest.mark.asyncio
async def test_concurrent_evaluators_only_cleanup_their_own_snapshot_session(
    tmp_path: Path,
) -> None:
    both_started = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()
    observed_roots: dict[str, Path] = {}

    def executor_for(label: str, release: asyncio.Event):
        async def executor(prompt: str, system_prompt: str):
            payload = json.loads(prompt)
            manifest_artifact = next(
                artifact
                for artifact in payload["artifact_backed_evidence"]["artifacts"]
                if artifact["kind"] == "evidence_manifest"
            )
            observed_roots[label] = Path(manifest_artifact["path"]).parent
            if len(observed_roots) == 2:
                both_started.set()
            await release.wait()
            return {"score": 80.0, "verdict": "Pass"}

        return executor

    first_backend = substrate_module.AgentJudgeBackend(
        backend_id="concurrent-first",
        system_prompt="judge",
        executor=executor_for("first", release_first),
        prompt_builder=_verified_evidence_prompt_builder(
            tmp_path / "concurrent-first",
            source_id="concurrent-first",
        ),
    )
    second_backend = substrate_module.AgentJudgeBackend(
        backend_id="concurrent-second",
        system_prompt="judge",
        executor=executor_for("second", release_second),
        prompt_builder=_verified_evidence_prompt_builder(
            tmp_path / "concurrent-second",
            source_id="concurrent-second",
        ),
    )
    suite = substrate_module.EvalSuiteDef(suite_id="cleanup-suite")
    first_task = asyncio.create_task(
        first_backend.execute({"input": "question"}, {"answer": "answer"}, suite)
    )
    second_task = asyncio.create_task(
        second_backend.execute({"input": "question"}, {"answer": "answer"}, suite)
    )
    await asyncio.wait_for(both_started.wait(), timeout=2)
    assert observed_roots["first"] != observed_roots["second"]
    assert observed_roots["first"].exists()
    assert observed_roots["second"].exists()

    release_first.set()
    await first_task
    assert observed_roots["first"].exists() is False
    assert observed_roots["second"].exists()

    release_second.set()
    await second_task
    assert observed_roots["second"].exists() is False
    assert (
        evaluator_runtime_module._VERIFIED_EVIDENCE_SNAPSHOT_ROOT.exists()
        is False
    )


def test_atexit_cleanup_removes_active_verified_snapshot_sessions(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "atexit" / "evidence_bundle.json"
    bundle_path.parent.mkdir()
    _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "atexit-cleanup",
                "artifact_path": str(tmp_path / "source.txt"),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {"bounded_excerpt": "verified evidence"},
            }
        ],
    )
    bundle = _load_prompt_evidence_bundle(str(bundle_path))
    session_root = Path(bundle["manifest"]["path"]).parent
    assert session_root.exists()

    evaluator_runtime_module._cleanup_all_verified_evidence_sessions()

    assert session_root.exists() is False
    assert evaluator_runtime_module._ACTIVE_VERIFIED_EVIDENCE_SESSIONS == {}
    assert (
        evaluator_runtime_module._VERIFIED_EVIDENCE_SNAPSHOT_ROOT.exists()
        is False
    )


def test_atexit_cleanup_does_not_remove_another_process_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "forked-atexit" / "evidence_bundle.json"
    bundle_path.parent.mkdir()
    _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "forked-atexit",
                "artifact_path": str(tmp_path / "source.txt"),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {"bounded_excerpt": "verified evidence"},
            }
        ],
    )
    bundle = _load_prompt_evidence_bundle(str(bundle_path))
    session_root = Path(bundle["manifest"]["path"]).parent
    creator_pid = os.getpid()

    monkeypatch.setattr(
        evaluator_runtime_module.os,
        "getpid",
        lambda: creator_pid + 1,
    )
    evaluator_runtime_module._cleanup_all_verified_evidence_sessions()
    assert session_root.exists()

    monkeypatch.setattr(
        evaluator_runtime_module.os,
        "getpid",
        lambda: creator_pid,
    )
    evaluator_runtime_module._cleanup_all_verified_evidence_sessions()
    assert session_root.exists() is False


def test_next_startup_reclaims_only_proven_stale_dead_pid_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "stale" / "evidence_bundle.json"
    bundle_path.parent.mkdir()
    _write_verified_evidence_bundle(
        bundle_path,
        [
            {
                "source_id": "stale-cleanup",
                "artifact_path": str(tmp_path / "source.txt"),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {"bounded_excerpt": "verified evidence"},
            }
        ],
    )
    bundle = _load_prompt_evidence_bundle(str(bundle_path))
    session_root = Path(bundle["manifest"]["path"]).parent
    evaluator_runtime_module._ACTIVE_VERIFIED_EVIDENCE_SESSIONS.clear()
    substrate_module._PRIVATE_ARTIFACT_SESSION_CLEANUPS.clear()
    monkeypatch.setattr(
        evaluator_runtime_module,
        "_VERIFIED_EVIDENCE_SESSION_STALE_AGE_SECONDS",
        0,
    )
    monkeypatch.setattr(
        evaluator_runtime_module,
        "_VERIFIED_EVIDENCE_STALE_RECLAIMED",
        False,
    )
    monkeypatch.setattr(
        evaluator_runtime_module,
        "_verified_evidence_pid_is_alive",
        lambda pid: True,
    )

    evaluator_runtime_module._reclaim_stale_verified_evidence_sessions()
    assert session_root.exists()

    session_root.chmod(0o755)
    monkeypatch.setattr(
        evaluator_runtime_module,
        "_VERIFIED_EVIDENCE_STALE_RECLAIMED",
        False,
    )
    monkeypatch.setattr(
        evaluator_runtime_module,
        "_verified_evidence_pid_is_alive",
        lambda pid: False,
    )
    evaluator_runtime_module._reclaim_stale_verified_evidence_sessions()
    assert session_root.exists()

    session_root.chmod(0o700)
    monkeypatch.setattr(
        evaluator_runtime_module,
        "_VERIFIED_EVIDENCE_STALE_RECLAIMED",
        False,
    )
    replacement_bundle_path = (
        tmp_path / "stale" / "replacement_evidence_bundle.json"
    )
    _write_verified_evidence_bundle(
        replacement_bundle_path,
        [
            {
                "source_id": "replacement-session",
                "artifact_path": str(tmp_path / "replacement-source.txt"),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {"bounded_excerpt": "replacement evidence"},
            }
        ],
    )
    replacement_bundle = _load_prompt_evidence_bundle(
        str(replacement_bundle_path)
    )
    replacement_session_root = Path(
        replacement_bundle["manifest"]["path"]
    ).parent

    assert session_root.exists() is False
    assert replacement_session_root.exists()
    evaluator_runtime_module._cleanup_all_verified_evidence_sessions()
    assert replacement_session_root.exists() is False
    assert (
        evaluator_runtime_module._VERIFIED_EVIDENCE_SNAPSHOT_ROOT.exists()
        is False
    )


def test_next_startup_reclaims_only_proven_legacy_snapshot_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_parent = (
        evaluator_runtime_module._VERIFIED_EVIDENCE_SNAPSHOT_ROOT.parent
    )
    uid = getattr(os, "getuid", lambda: 0)()
    dead_pid = 999_999
    alive_pid = os.getpid()
    stale_ns = (
        evaluator_runtime_module.time.time_ns()
        - 120 * 1_000_000_000
    )

    def create_legacy_root(
        *,
        pid: int,
        token: str,
        content: bytes,
        digest: str | None = None,
        stale: bool = True,
        mode: int = 0o700,
    ) -> Path:
        root = legacy_parent / (
            f"aworld-evaluator-verified-evidence-{uid}-{pid}-{token}"
        )
        root.mkdir(mode=0o700)
        resolved_digest = digest or hashlib.sha256(content).hexdigest()
        snapshot = root / f"evidence-manifest-{resolved_digest}.jsonl"
        snapshot.write_bytes(content)
        snapshot.chmod(0o400)
        if stale:
            os.utime(snapshot, ns=(stale_ns, stale_ns))
            os.utime(root, ns=(stale_ns, stale_ns))
        root.chmod(mode)
        return root

    valid_root = create_legacy_root(
        pid=dead_pid,
        token="1" * 16,
        content=b'{"source_id":"valid"}\n',
    )
    alive_root = create_legacy_root(
        pid=alive_pid,
        token="2" * 16,
        content=b'{"source_id":"alive"}\n',
    )
    fresh_root = create_legacy_root(
        pid=dead_pid,
        token="3" * 16,
        content=b'{"source_id":"fresh"}\n',
        stale=False,
    )
    permissive_root = create_legacy_root(
        pid=dead_pid,
        token="4" * 16,
        content=b'{"source_id":"permissive"}\n',
        mode=0o755,
    )
    corrupt_root = create_legacy_root(
        pid=dead_pid,
        token="5" * 16,
        content=b'{"source_id":"corrupt"}\n',
        digest="0" * 64,
    )
    monkeypatch.setattr(
        evaluator_runtime_module,
        "_VERIFIED_EVIDENCE_SESSION_STALE_AGE_SECONDS",
        60,
    )
    monkeypatch.setattr(
        evaluator_runtime_module,
        "_VERIFIED_EVIDENCE_STALE_RECLAIMED",
        False,
    )
    monkeypatch.setattr(
        evaluator_runtime_module,
        "_verified_evidence_pid_is_alive",
        lambda pid: pid == alive_pid,
    )

    evaluator_runtime_module._reclaim_stale_verified_evidence_sessions()

    assert valid_root.exists() is False
    assert alive_root.exists()
    assert fresh_root.exists()
    assert permissive_root.exists()
    assert corrupt_root.exists()
    assert (
        evaluator_runtime_module._VERIFIED_EVIDENCE_SNAPSHOT_ROOT.exists()
        is False
    )


def test_trajectory_prompt_artifact_index_lists_all_bundle_source_artifacts(
    tmp_path: Path,
) -> None:
    entries = []
    expected_paths = set()
    for index in range(7):
        source_path = tmp_path / f"source-{index}.txt"
        source_path.write_text(f"source evidence {index}", encoding="utf-8")
        expected_paths.add(str(source_path))
        entries.append(
            {
                "source_id": f"source-{index}",
                "artifact_path": str(source_path),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {"excerpt": f"bounded evidence {index}"},
            }
        )
    bundle_path = tmp_path / "evidence_bundle.json"
    _write_verified_evidence_bundle(bundle_path, entries)

    prompt = json.loads(
        _build_trajectory_prompt(
            {"input": "question"},
            {
                "case_id": "case-1",
                "answer": "answer",
                "trajectory": [
                    {
                        "state": {"input": {"content": "question"}, "messages": []},
                        "meta": {"step": 1, "agent_id": "Aworld"},
                        "action": {"content": "answer", "is_agent_finished": "True"},
                    }
                ],
                "evidence_bundle_path": str(bundle_path),
            },
            suite=None,
        )
    )

    prompt_entries = prompt["extracted_trajectory"]["evidence_bundle"]["entries"]
    source_artifact_paths = {
        artifact["path"]
        for artifact in prompt["artifact_backed_evidence"]["artifacts"]
        if artifact["kind"] == "source_artifact"
    }
    assert len(prompt_entries) == 5
    assert source_artifact_paths == expected_paths
    assert prompt["extracted_trajectory"]["evidence_bundle"]["valid"] is True
    assert prompt["evidence_summary"]["canonical_bundle_entry_count"] == 7


def test_trajectory_prompt_artifact_index_rejects_bundle_paths_outside_trusted_roots(
    tmp_path: Path,
) -> None:
    trusted_dir = tmp_path / "trusted"
    trusted_dir.mkdir()
    trusted_source = trusted_dir / "source.txt"
    trusted_source.write_text("trusted evidence", encoding="utf-8")
    untrusted_source = tmp_path / "outside" / "secret.txt"
    untrusted_source.parent.mkdir()
    untrusted_source.write_text("secret evidence", encoding="utf-8")
    untrusted_manifest = untrusted_source.parent / "evidence_manifest.jsonl"
    untrusted_manifest.write_text("{}\n", encoding="utf-8")
    bundle_path = trusted_dir / "evidence_bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "format": "aworld.self_evolve.evidence_bundle",
                "version": 1,
                "valid": True,
                "manifest": {
                    "path": str(untrusted_manifest),
                    "present": True,
                    "readable": True,
                    "valid": True,
                    "entry_count": 1,
                    "invalid_entry_count": 0,
                    "size_bytes": untrusted_manifest.stat().st_size,
                    "fingerprint": "sha256:untrusted",
                },
                "entries": [
                    {
                        "source_id": "trusted",
                        "artifact_path": str(trusted_source),
                        "bounded_evidence": {"excerpt": "trusted evidence"},
                    },
                    {
                        "source_id": "untrusted",
                        "artifact_path": str(untrusted_source),
                        "bounded_evidence": {"excerpt": "untrusted evidence"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    extracted_path = trusted_dir / "extracted.json"
    extracted_path.write_text(
        json.dumps(
            {
                "task_id": "case-1",
                "question": "question",
                "steps": [{"step": 1, "is_agent_finished": True}],
                "final_answer": "answer",
                "evidence": [],
                "evidence_bundle_path": str(bundle_path),
            }
        ),
        encoding="utf-8",
    )

    prompt = json.loads(
        _build_trajectory_prompt(
            {"input": "question"},
            {
                "case_id": "case-1",
                "answer": "answer",
                "artifacts": {"outcome": {"extracted_path": str(extracted_path)}},
            },
            suite=None,
        )
    )

    source_artifact_paths = {
        artifact["path"]
        for artifact in prompt["artifact_backed_evidence"]["artifacts"]
        if artifact["kind"] == "source_artifact"
    }
    assert str(trusted_source) in source_artifact_paths
    assert str(untrusted_source) not in source_artifact_paths
    assert all(
        artifact["kind"] != "evidence_manifest"
        for artifact in prompt["artifact_backed_evidence"]["artifacts"]
    )


def test_trajectory_prompt_compacts_noisy_evidence_without_losing_quality_signals() -> None:
    noisy_content = "alpha " * 2000
    prompt = json.loads(
        _build_trajectory_prompt(
            {"input": "question"},
            {
                "case_id": "case-1",
                "answer": "answer",
                "trajectory": [
                    {
                        "state": {
                            "input": {"content": "question"},
                            "messages": [
                                {
                                    "role": "tool",
                                    "content": noisy_content,
                                }
                            ],
                        },
                        "meta": {"step": 1, "agent_id": "Aworld"},
                        "action": {"content": "answer", "is_agent_finished": "True"},
                    }
                ],
            },
            suite=None,
        )
    )

    evidence = prompt["extracted_trajectory"]["evidence"][0]
    evidence_summary = prompt["evidence_summary"]
    assert len(evidence["content"]) < len(noisy_content)
    assert evidence["prompt_compacted"] is True
    assert evidence["original_length"] == len(noisy_content)
    assert evidence["content"].startswith("alpha")
    assert "omitted" in evidence["content"]
    assert evidence_summary["evidence_block_count"] == 1
    assert evidence_summary["prompt_compacted_count"] == 1
    assert evidence_summary["total_original_chars"] == len(noisy_content)
    assert evidence_summary["sources"] == ["state.messages"]


def test_run_evaluator_source_cli_passes_source_fields_to_hooks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "answers.jsonl"
    input_path.write_text('{"id":"case-1","input":"question","answer":"existing"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")
    events: list[tuple[str, dict]] = []

    class CaptureHook:
        def __init__(self, hook_point: str):
            self.hook_point = hook_point

        async def run(self, *, event, state):
            events.append((self.hook_point, dict(event)))
            return {"metadata": {"hook_tag": "source-hook"}}

    async def fake_run_evaluation_flow(flow):
        assert flow.target["hook_tag"] == "source-hook"
        return {
            "report_version": 1,
            "suite_id": "answer-source-evaluator",
            "summary": {"answer-source-evaluator": {"score": {"mean": 0.9}}},
            "metrics": {"score": {"mean": 0.9}},
            "results": [],
            "result_counts": {"cases_total": 0, "cases_with_metrics": 0, "cases_with_judge": 0},
            "approval": {"required": False, "resolved": False, "approved": None},
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
        }

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._load_evaluator_hooks",
        lambda: {
            "evaluator.pre_run": (CaptureHook("pre"),),
            "evaluator.post_run": (CaptureHook("post"),),
        },
    )
    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    run_evaluator_source_cli(
        input=str(input_path),
        kind="answer",
        judge_agent=str(judge_agent),
        task_id="case-1",
        output=str(tmp_path / "report.json"),
    )

    assert events[0][0] == "pre"
    assert events[0][1]["mode"] == "source"
    assert events[0][1]["input"] == str(input_path.resolve())
    assert events[0][1]["kind"] == "answer"
    assert events[0][1]["task_id"] == "case-1"
    assert events[0][1]["judge_agent"] == str(judge_agent.resolve())
    assert events[1][0] == "post"
    assert events[1][1]["mode"] == "source"
    assert events[1][1]["report"]["source_selection"]["kind"] == "answer"


def test_run_evaluator_source_cli_persists_schema_valid_source_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "answers.jsonl"
    input_path.write_text('{"id":"case-1","input":"question","answer":"existing"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")

    async def fake_run_evaluation_flow(flow):
        return {
            "report_version": 1,
            "report_format": {"id": "aworld.evaluator.report", "version": 1},
            "generated_at": "2026-06-10T00:00:00Z",
            "suite_id": "answer-source-evaluator",
            "target": flow.target,
            "judge_backend": {"backend_id": "source-agent-md"},
            "summary": {"answer-source-evaluator": {"score": {"mean": 88.0}}},
            "metrics": {"score": {"mean": 88.0}},
            "results": [
                {
                    "case_id": "case-1",
                    "input": {"input": "question"},
                    "metrics": {"score": {"value": 88.0, "status": "PASSED"}},
                    "judge": {"score": 88.0, "verdict": "Pass"},
                    "judge_backend": {"backend_id": "source-agent-md"},
                    "state_summary": {"answer": "existing"},
                }
            ],
            "result_counts": {"cases_total": 1, "cases_with_metrics": 1, "cases_with_judge": 1},
            "gate": {"status": "pass", "metric_name": "score", "value": 88.0},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_source_cli(
        input=str(input_path),
        kind="answer",
        judge_agent=str(judge_agent),
        output=str(tmp_path / "report.json"),
    )

    validate_evaluator_report(report)


@pytest.mark.asyncio
async def test_framework_run_evaluation_flow_returns_report_object() -> None:
    async def fake_judge(case_input, target):
        return {"score": 0.9}

    flow = substrate_module.EvaluationFlowDef(
        target={"kind": "file", "target_path": "artifact.txt"},
        suite=substrate_module.EvalSuiteDef(
            suite_id="app-evaluator",
            cases=[substrate_module.EvalCaseDef(case_id="case-1", input={"query": "demo"})],
            gate_policy=substrate_module.GatePolicyDef(metric_name="score", pass_threshold=0.0),
            judge=fake_judge,
        ),
    )

    report = await substrate_module.run_evaluation_flow(flow)

    assert isinstance(report, EvaluatorReport)
    assert report["suite_id"] == "app-evaluator"


def test_run_evaluator_cli_writes_default_report_when_output_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    async def fake_run_evaluation_flow(flow):
        return {
            "report_version": 1,
            "suite_id": "app-evaluator",
            "judge_backend": {"backend_id": "stub-agent"},
            "summary": {"app-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_cli(target=str(target))

    report_path = Path(report["report_path"])
    persisted = json.loads(report_path.read_text(encoding="utf-8"))

    assert report_path.exists()
    assert report_path.parent == tmp_path / ".aworld" / "evaluations"
    assert persisted["suite_id"] == "app-evaluator"


def test_available_evaluator_suites_lists_builtin_suite() -> None:
    suites = available_evaluator_suites()

    assert "app-evaluator" in suites


def test_cli_schema_helpers_delegate_to_framework_sources() -> None:
    assert get_declared_evaluator_suite_schema() == get_declared_eval_suite_schema()
    assert get_evaluator_report_schema()["title"] == "AWorld Evaluator Report"


def test_available_evaluator_suites_filters_by_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.png"
    target.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aA1EAAAAASUVORK5CYII="
        )
    )

    suites = available_evaluator_suites(target=str(target))

    assert suites == ["app-evaluator"]


def test_available_evaluator_suites_loads_declared_suites_from_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_dir = tmp_path / ".aworld" / "evaluators"
    manifest_dir.mkdir(parents=True)
    target = tmp_path / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")
    (manifest_dir / "strict-ui.json").write_text(
        """
{
  "suite_id": "strict-ui",
  "base_suite": "app-evaluator",
  "target_kinds": ["file"]
}
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    suites = available_evaluator_suites(target=str(target))

    assert "strict-ui" in suites


def test_available_evaluator_suites_uses_target_workspace_not_process_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "project"
    manifest_dir = workspace / ".aworld" / "evaluators"
    manifest_dir.mkdir(parents=True)
    target = workspace / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")
    (manifest_dir / "strict-ui.json").write_text(
        """
{
  "suite_id": "strict-ui",
  "base_suite": "app-evaluator",
  "target_kinds": ["file"]
}
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    suites = available_evaluator_suites(target=str(target))

    assert "strict-ui" in suites


def test_run_evaluator_cli_marks_image_targets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.png"
    target.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aA1EAAAAASUVORK5CYII="
        )
    )

    async def fake_run_evaluation_flow(flow):
        assert flow.target["target_kind"] == "image"
        return {
            "report_version": 1,
            "suite_id": "app-evaluator",
            "judge_backend": {"backend_id": "stub-agent"},
            "summary": {"app-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_cli(target=str(target))

    assert report["suite_id"] == "app-evaluator"


def test_run_evaluator_cli_records_suite_selection_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")

    async def fake_run_evaluation_flow(flow):
        return {
            "report_version": 1,
            "suite_id": "app-evaluator",
            "judge_backend": {"backend_id": "stub-agent"},
            "summary": {"app-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_cli(target=str(target))

    assert report["suite_selection"]["mode"] == "auto"
    assert report["suite_selection"]["resolved"] == "app-evaluator"


def test_run_evaluator_cli_adds_automation_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")

    async def fake_run_evaluation_flow(flow):
        return {
            "report_version": 1,
            "suite_id": "app-evaluator",
            "judge_backend": {"backend_id": "stub-agent"},
            "summary": {"app-evaluator": {"score": {"mean": 0.7}}},
            "metrics": {"score": {"mean": 0.7}},
            "result_counts": {"cases_total": 2, "cases_with_metrics": 2, "cases_with_judge": 2},
            "results": [{}, {}],
            "gate": {"status": "needs_approval", "metric_name": "score", "value": 0.7},
            "approval": {"required": True, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_cli(target=str(target))

    assert report["automation"]["gate_status"] == "needs_approval"
    assert report["automation"]["case_count"] == 2
    assert report["automation"]["judge_backend"] == "stub-agent"
    assert report["automation"]["suggested_exit_code"] == 3


def test_run_evaluator_cli_rejects_missing_target(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.txt"

    with pytest.raises(FileNotFoundError, match="does not exist"):
        run_evaluator_cli(target=str(missing))


def test_evaluator_exit_code_matches_gate_and_approval() -> None:
    assert evaluator_exit_code({"gate": {"status": "pass"}, "approval": {}}) == 0
    assert evaluator_exit_code({"gate": {"status": "fail"}, "approval": {}}) == 2
    assert evaluator_exit_code(
        {"gate": {"status": "needs_approval"}, "approval": {"approved": False}}
    ) == 3


def test_get_evaluator_report_schema_describes_report_contract() -> None:
    schema = get_evaluator_report_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "AWorld Evaluator Report"
    assert "report_format" in schema["required"]
    assert schema["properties"]["report_format"]["properties"]["id"]["const"] == "aworld.evaluator.report"
    assert schema["properties"]["report_format"]["properties"]["version"]["const"] == 1
    assert schema["properties"]["metrics"]["additionalProperties"]["$ref"] == "#/$defs/metricAggregate"
    assert (
        schema["properties"]["results"]["items"]["properties"]["metrics"]["additionalProperties"]["$ref"]
        == "#/$defs/caseMetric"
    )
    assert schema["properties"]["gate"]["$ref"] == "#/$defs/gateDecision"
    assert schema["properties"]["automation"]["$ref"] == "#/$defs/automationSummary"
    assert schema["$defs"]["gateDecision"]["properties"]["status"]["enum"] == ["pass", "fail", "needs_approval"]
    assert schema["$defs"]["automationSummary"]["properties"]["suggested_exit_code"]["enum"] == [0, 2, 3]
    assert schema["$defs"]["automationSummary"]["required"] == [
        "gate_status",
        "metric_name",
        "metric_value",
        "approval_required",
        "approval_resolved",
        "approved",
        "suggested_exit_code",
        "case_count",
        "judge_backend",
    ]


def test_validate_evaluator_report_accepts_valid_report() -> None:
    report = {
        "report_version": 1,
        "report_format": {"id": "aworld.evaluator.report", "version": 1},
        "generated_at": "2026-06-02T04:00:00Z",
        "suite_id": "app-evaluator",
        "target": {"target_path": "/tmp/artifact.txt", "target_kind": "file"},
        "summary": {"app-evaluator": {"score": {"mean": 0.9}}},
        "metrics": {"score": {"mean": 0.9, "min": 0.9, "max": 0.9, "std": 0.0, "eval_status": "PASSED"}},
        "results": [
            {
                "case_id": "artifact.txt",
                "input": {"target_path": "/tmp/artifact.txt"},
                "metrics": {"score": {"value": 0.9, "status": "PASSED"}},
                "judge": {"score": 0.9},
                "judge_backend": {"backend_id": "stub-agent"},
            }
        ],
        "result_counts": {"cases_total": 1, "cases_with_metrics": 1, "cases_with_judge": 1},
        "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
        "approval": {"required": False, "resolved": False, "approved": None},
        "automation": {
            "gate_status": "pass",
            "metric_name": "score",
            "metric_value": 0.9,
            "approval_required": False,
            "approval_resolved": False,
            "approved": None,
            "suggested_exit_code": 0,
            "case_count": 1,
            "judge_backend": "stub-agent",
        },
    }

    validate_evaluator_report(report)


def test_validate_and_render_categorical_gate_report() -> None:
    report = {
        "report_version": 1,
        "report_format": {"id": "aworld.evaluator.report", "version": 1},
        "generated_at": "2026-06-02T04:00:00Z",
        "suite_id": "categorical-suite",
        "target": {"target_path": "/tmp/artifact.txt", "target_kind": "file"},
        "summary": {"categorical-suite": {"verdict": {"value": "approved"}}},
        "metrics": {"verdict": {"value": "approved", "eval_status": "PASSED"}},
        "results": [
            {
                "case_id": "artifact.txt",
                "input": {"target_path": "/tmp/artifact.txt"},
                "metrics": {"verdict": {"value": "approved", "status": "PASSED"}},
                "judge": {"score": 1.0, "verdict": "approved"},
            }
        ],
        "result_counts": {"cases_total": 1, "cases_with_metrics": 1, "cases_with_judge": 1},
        "gate": {"status": "pass", "metric_name": "verdict", "value": "approved"},
        "approval": {"required": False, "resolved": False, "approved": None},
        "automation": {
            "gate_status": "pass",
            "metric_name": "verdict",
            "metric_value": "approved",
            "approval_required": False,
            "approval_resolved": False,
            "approved": None,
            "suggested_exit_code": 0,
            "case_count": 1,
            "judge_backend": None,
        },
    }

    validate_evaluator_report(report)

    assert "approved" in render_evaluator_summary(report)


def test_validate_evaluator_report_rejects_invalid_gate_status() -> None:
    report = {
        "report_version": 1,
        "report_format": {"id": "aworld.evaluator.report", "version": 1},
        "generated_at": "2026-06-02T04:00:00Z",
        "suite_id": "app-evaluator",
        "target": {"target_path": "/tmp/artifact.txt", "target_kind": "file"},
        "summary": {"app-evaluator": {"score": {"mean": 0.9}}},
        "metrics": {"score": {"mean": 0.9}},
        "results": [],
        "result_counts": {"cases_total": 0, "cases_with_metrics": 0, "cases_with_judge": 0},
        "gate": {"status": "maybe", "metric_name": "score", "value": 0.9},
        "approval": {"required": False, "resolved": False, "approved": None},
        "automation": {
            "gate_status": "maybe",
            "metric_name": "score",
            "metric_value": 0.9,
            "approval_required": False,
            "approval_resolved": False,
            "approved": None,
            "suggested_exit_code": 0,
            "case_count": 0,
            "judge_backend": None,
        },
    }

    with pytest.raises(ValueError, match="status"):
        validate_evaluator_report(report)


def test_get_declared_evaluator_suite_schema_describes_manifest_contract() -> None:
    schema = get_declared_evaluator_suite_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "AWorld Declared Evaluator Suite"
    assert schema["properties"]["base_suite"]["const"] == "app-evaluator"
    assert "suite_id" in schema["required"]
    assert "target_kinds" in schema["properties"]
