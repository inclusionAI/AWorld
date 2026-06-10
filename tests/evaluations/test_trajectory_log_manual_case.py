from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

import pytest
from pydantic import BaseModel, model_validator

from aworld.config.task_loader import _load_skill_agent
from aworld.evaluations.runtime_composition import RolloutState
from aworld.evaluations.substrate import (
    EvalCaseDef,
    EvalSuiteDef,
    EvaluationFlowDef,
    GateMetricCondition,
    GatePolicyDef,
    JudgeExecution,
    JudgeSchemaDef,
    StateCheckGrader,
    _coerce_judge_payload,
    run_evaluation_flow,
)
from aworld.evaluations.report import validate_evaluator_report
from aworld.runner import Runners
from aworld.utils.skill_loader import extract_front_matter


DEFAULT_JUDGE_TIMEOUT_SECONDS = 600.0


class _FakePytestConfig:
    def __init__(self, values: Mapping[str, Any]):
        self._values = values

    def getoption(self, name: str) -> Any:
        return self._values.get(name)


class TrajectoryEvalJudgeOutput(BaseModel):
    score: float
    verdict: Literal["Excellent", "Pass", "Marginal", "Fail"]
    A1_groundedness: int
    A2_completeness: int
    A3_relevance: int
    A4_readability: int
    B1_tool_use: int
    B2_efficiency: int
    B3_compliance: int
    B4_robustness: int
    veto_triggered: bool = False

    @model_validator(mode="before")
    @classmethod
    def flatten_agent_report(cls, value: Any) -> Any:
        if not isinstance(value, Mapping) or "dimensions" not in value:
            return value
        flattened = dict(value)
        if "score" not in flattened and "weighted_score" in flattened:
            flattened["score"] = flattened["weighted_score"]
        dimensions = value.get("dimensions") or {}
        for metric_name in (
            "A1_groundedness",
            "A2_completeness",
            "A3_relevance",
            "A4_readability",
            "B1_tool_use",
            "B2_efficiency",
            "B3_compliance",
            "B4_robustness",
        ):
            metric_payload = dimensions.get(metric_name) if isinstance(dimensions, Mapping) else None
            if isinstance(metric_payload, Mapping) and "score" in metric_payload:
                flattened[metric_name] = metric_payload["score"]
        return flattened


def _truthy_string(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1"}


def _manual_replay_config(pytest_config: Any) -> dict[str, Any]:
    required_options = {
        "--task-id": pytest_config.getoption("trajectory_task_id"),
        "--trajectory-log": pytest_config.getoption("trajectory_log"),
        "--agent-prompt": pytest_config.getoption("trajectory_agent_prompt"),
        "--out-dir": pytest_config.getoption("trajectory_out_dir"),
    }
    missing = [name for name, value in required_options.items() if not value]
    if missing:
        raise pytest.UsageError(
            "manual trajectory replay requires explicit pytest options: "
            + ", ".join(missing)
        )

    task_id = required_options["--task-id"]
    log_path = Path(str(required_options["--trajectory-log"])).expanduser()
    agent_prompt_path = Path(str(required_options["--agent-prompt"]))
    out_dir = Path(str(required_options["--out-dir"]))
    judge_timeout_seconds = pytest_config.getoption("trajectory_judge_timeout") or DEFAULT_JUDGE_TIMEOUT_SECONDS
    return {
        "task_id": str(task_id),
        "log_path": log_path,
        "agent_prompt_path": agent_prompt_path,
        "out_dir": out_dir,
        "judge_timeout_seconds": float(judge_timeout_seconds),
    }


def _safe_skill_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-._") or "markdown-agent"


def _frontmatter_scalar(value: Any, default: str) -> str:
    text = str(value if value not in (None, "") else default)
    return " ".join(text.splitlines()).strip()


def _normalize_tool_list(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return {}


def _materialize_agent_markdown_as_skill(
    agent_markdown_path: Path,
    *,
    skills_root: Path,
    skill_name: str,
) -> Path:
    lines = agent_markdown_path.read_text(encoding="utf-8").splitlines()
    frontmatter, body_start = extract_front_matter(lines)
    body = "\n".join(lines[body_start:]).strip()
    description = _frontmatter_scalar(
        frontmatter.get("description", frontmatter.get("desc")),
        f"Agent loaded from {agent_markdown_path}",
    )
    tool_list = _normalize_tool_list(frontmatter.get("tool_list", {}))

    skill_dir = skills_root / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\n"
        f"name: {_frontmatter_scalar(frontmatter.get('name'), skill_name)}\n"
        f"description: {description}\n"
        "type: agent\n"
        f"tool_list: {json.dumps(tool_list, ensure_ascii=False)}\n"
        "---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return skill_path


async def _load_agent_markdown_as_aworld_agent(agent_markdown_path: Path, *, agent_id: str) -> Any:
    skill_name = _safe_skill_name(agent_id)
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    with tempfile.TemporaryDirectory(prefix="aworld-agent-md-") as tmp_dir:
        skills_root = Path(tmp_dir) / "skills"
        _materialize_agent_markdown_as_skill(
            agent_markdown_path,
            skills_root=skills_root,
            skill_name=skill_name,
        )
        return await _load_skill_agent(
            agent_id=agent_id,
            agent_def={
                "skill_name": skill_name,
                "config": {
                    "llm_config": {
                        "llm_model_name": os.getenv("LLM_MODEL_NAME"),
                        "llm_provider": os.getenv("LLM_PROVIDER"),
                        "llm_api_key": api_key,
                        "llm_base_url": os.getenv("LLM_BASE_URL"),
                    }
                },
            },
            skills_path=skills_root,
            global_mcp_config=None,
        )


@dataclass(frozen=True)
class MarkdownAgentJudgeBackend:
    backend_id: str
    agent_markdown_path: Path
    prompt_builder: Any
    timeout_seconds: float | None = None

    def is_available(self) -> bool:
        model_name = os.getenv("LLM_MODEL_NAME")
        api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        return self.agent_markdown_path.exists() and bool(model_name and api_key)

    async def execute(self, case_input: dict[str, Any], target: dict[str, Any], suite: EvalSuiteDef) -> JudgeExecution:
        if not self.is_available():
            raise RuntimeError(f"judge backend '{self.backend_id}' is not available")

        prompt = self.prompt_builder(case_input, target, suite)
        if isinstance(prompt, tuple):
            raise ValueError("MarkdownAgentJudgeBackend only supports text prompts in this manual replay test")

        agent = await _load_agent_markdown_as_aworld_agent(
            self.agent_markdown_path,
            agent_id=self.backend_id,
        )

        async def _run_agent() -> str:
            response = await Runners.run(input=str(prompt), agent=agent)
            return str(getattr(response, "answer", response))

        if self.timeout_seconds is not None:
            response_text = await asyncio.wait_for(_run_agent(), timeout=self.timeout_seconds)
        else:
            response_text = await _run_agent()
        return JudgeExecution(backend_id=self.backend_id, payload=_coerce_judge_payload(response_text))


def test_manual_replay_config_requires_explicit_pytest_options(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWORLD_TRAJECTORY_TASK_ID", "task_from_env")
    monkeypatch.setenv("AWORLD_TRAJECTORY_LOG", "~/env/trajectory.log")
    monkeypatch.setenv("AWORLD_TRAJECTORY_AGENT_PROMPT", "env/agent.md")
    monkeypatch.setenv("AWORLD_TRAJECTORY_OUT_DIR", "env/reports")

    with pytest.raises(pytest.UsageError, match="--task-id"):
        _manual_replay_config(_FakePytestConfig({}))

    config = _manual_replay_config(
        _FakePytestConfig(
            {
                "trajectory_task_id": "task_from_cli",
                "trajectory_log": "~/cli/trajectory.log",
                "trajectory_agent_prompt": "cli/agent.md",
                "trajectory_out_dir": "cli/reports",
                "trajectory_judge_timeout": 12.5,
            }
        )
    )

    assert config["task_id"] == "task_from_cli"
    assert config["log_path"] == Path("~/cli/trajectory.log").expanduser()
    assert config["agent_prompt_path"] == Path("cli/agent.md")
    assert config["out_dir"] == Path("cli/reports")
    assert config["judge_timeout_seconds"] == 12.5


@pytest.mark.asyncio
async def test_agent_markdown_loads_as_aworld_agent_via_existing_skill_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("LLM_MODEL_NAME", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    agent_md = tmp_path / "agent.md"
    agent_md.write_text(
        "---\n"
        "name: custom trajectory judge\n"
        "description: Evaluates trajectories\n"
        "tools: Bash, Read\n"
        "model: opus\n"
        "---\n\n"
        "# Judge Contract\n"
        "Return strict JSON.\n",
        encoding="utf-8",
    )

    agent = await _load_agent_markdown_as_aworld_agent(agent_md, agent_id="custom-judge")

    assert agent.name() == "custom-judge"
    assert agent.desc() == "Evaluates trajectories"
    assert agent.mcp_servers == []
    assert "Return strict JSON." in agent.system_prompt


@pytest.mark.asyncio
async def test_markdown_agent_judge_backend_runs_loaded_agent_with_runners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("LLM_MODEL_NAME", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    agent_md = tmp_path / "agent.md"
    agent_md.write_text(
        "---\n"
        "name: trajectory judge\n"
        "description: Test judge\n"
        "---\n\n"
        "You are the test judge.\n",
        encoding="utf-8",
    )
    calls: dict[str, Any] = {}

    class _FakeTaskResponse:
        answer = json.dumps(
            {
                "weighted_score": 88,
                "verdict": "Pass",
                "dimensions": {"A1_groundedness": {"score": 4}},
            }
        )

    async def fake_run(input: str, agent: Any, **kwargs: Any) -> _FakeTaskResponse:
        calls["input"] = input
        calls["agent_name"] = agent.name()
        calls["system_prompt"] = agent.system_prompt
        return _FakeTaskResponse()

    monkeypatch.setattr("aworld.runner.Runners.run", fake_run)

    backend = MarkdownAgentJudgeBackend(
        backend_id="trajectory-evaluator-agent-md",
        agent_markdown_path=agent_md,
        prompt_builder=lambda case_input, target, suite: "judge this trajectory",
    )
    execution = await backend.execute({}, {}, object())

    assert calls == {
        "input": "judge this trajectory",
        "agent_name": "trajectory-evaluator-agent-md",
        "system_prompt": "You are the test judge.",
    }
    assert execution.backend_id == "trajectory-evaluator-agent-md"
    assert execution.payload["weighted_score"] == 88
    assert execution.payload["dimensions"]["A1_groundedness"]["score"] == 4


def test_trajectory_step_assertion_uses_extracted_num_steps(tmp_path: Path):
    extracted_path = tmp_path / "extracted_task.json"
    extracted_path.write_text(json.dumps({"num_steps": 81}), encoding="utf-8")
    result = {
        "state_summary": {"trajectory_steps": 81},
        "metadata": {"extracted_path": str(extracted_path)},
    }

    _assert_report_trajectory_steps_match_extracted(result)


def _assert_report_trajectory_steps_match_extracted(result: Mapping[str, Any]) -> None:
    extracted_path = Path(str(result["metadata"]["extracted_path"]))
    extracted = json.loads(extracted_path.read_text(encoding="utf-8"))
    assert result["state_summary"]["trajectory_steps"] == extracted["num_steps"]


def _extract_trajectory_record(log_path: Path, task_id: str) -> dict[str, Any]:
    target_line = None
    with log_path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if task_id in line:
                target_line = line
                break
    if target_line is None:
        raise AssertionError(f"task_id {task_id} not found in {log_path}")

    clean = re.sub(r"\x1b\[[0-9;]*m", "", target_line).strip()
    record = ast.literal_eval(clean)
    trajectory = json.loads(record["trajectory"])

    question = (trajectory[0].get("state", {}).get("input", {}) or {}).get("content")
    system_prompt = ""
    first_messages = trajectory[0].get("state", {}).get("messages", []) or []
    if first_messages and first_messages[0].get("role") == "system":
        system_prompt = str(first_messages[0].get("content") or "")

    steps = []
    final_answer = None
    for item in trajectory:
        meta = item.get("meta", {})
        action = item.get("action") or {}
        calls = []
        for tool_call in action.get("tool_calls") or []:
            function = tool_call.get("function") or {}
            calls.append({"name": function.get("name"), "arguments": str(function.get("arguments"))})
        finished = _truthy_string(action.get("is_agent_finished"))
        steps.append(
            {
                "step": meta.get("step"),
                "pre_agent": meta.get("pre_agent"),
                "agent_id": meta.get("agent_id"),
                "tool_calls": calls,
                "assistant_content": str(action.get("content") or ""),
                "is_agent_finished": finished,
            }
        )
        if finished and action.get("content"):
            final_answer = str(action.get("content"))

    final_messages = trajectory[-1].get("state", {}).get("messages", []) or []
    evidence = [
        {"msg_index": index, "content": str(message.get("content") or "")}
        for index, message in enumerate(final_messages)
        if message.get("role") == "tool"
    ]

    return {
        "task_id": task_id,
        "is_sub_task": record.get("is_sub_task"),
        "num_steps": len(trajectory),
        "question": question,
        "system_prompt_excerpt": system_prompt[:8000],
        "steps": steps,
        "final_answer": final_answer,
        "evidence": evidence,
    }


class TrajectoryLogReplayHarness:
    def __init__(self, *, out_dir: Path):
        self.out_dir = out_dir

    async def run_rollout(self, *, case: EvalCaseDef, target: Mapping[str, Any]) -> RolloutState:
        log_path = Path(str(case.input["trajectory_log"])).expanduser()
        task_id = str(case.input["task_id"])
        extracted = _extract_trajectory_record(log_path, task_id)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        extracted_path = self.out_dir / f"extracted_{task_id}.json"
        extracted_path.write_text(json.dumps(extracted, ensure_ascii=False, indent=2), encoding="utf-8")

        final_answer = extracted.get("final_answer") or ""
        is_finished = any(step.get("is_agent_finished") for step in extracted["steps"])
        return RolloutState(
            case_id=case.case_id,
            status="success" if is_finished and final_answer else "failed",
            answer=final_answer,
            trajectory=list(extracted["steps"]),
            outcome={
                "task_id": task_id,
                "question": extracted.get("question"),
                "evidence_blocks": len(extracted["evidence"]),
                "num_steps": extracted["num_steps"],
                "is_finished": is_finished,
                "final_answer_len": len(final_answer),
                "extracted_path": str(extracted_path),
            },
            metadata={
                "trajectory_log": str(log_path),
                "judge_agent_prompt": str(case.input["judge_agent_prompt"]),
                "extracted_path": str(extracted_path),
            },
        )


def _trajectory_judge_prompt(case_input: dict[str, Any], target: dict[str, Any], suite: EvalSuiteDef) -> str:
    outcome = (target.get("artifacts") or {}).get("outcome") or {}
    extracted_path = outcome.get("extracted_path")
    extracted_payload: dict[str, Any] = {}
    if extracted_path:
        extracted_payload = json.loads(Path(str(extracted_path)).read_text(encoding="utf-8"))

    payload = {
        "case": {
            "task_id": case_input["task_id"],
            "trajectory_log": case_input["trajectory_log"],
        },
        "extracted_trajectory": extracted_payload,
        "required_output_schema": {
            "score": "number, weighted score from 0 to 100",
            "verdict": "Excellent|Pass|Marginal|Fail",
            "A1_groundedness": "integer 1-5",
            "A2_completeness": "integer 1-5",
            "A3_relevance": "integer 1-5",
            "A4_readability": "integer 1-5",
            "B1_tool_use": "integer 1-5",
            "B2_efficiency": "integer 1-5",
            "B3_compliance": "integer 1-5",
            "B4_robustness": "integer 1-5",
            "veto_triggered": "boolean",
        },
        "instruction": (
            "Apply the trajectory-evaluator agent contract to the extracted trajectory. "
            "Do not call tools and do not re-read the raw log; all required evidence is in extracted_trajectory. "
            "Return exactly one JSON object matching required_output_schema, with no markdown."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@pytest.mark.asyncio
async def test_manual_trajectory_log_case_runs_end_to_end_for_human_replay(request: pytest.FixtureRequest):
    try:
        config = _manual_replay_config(request.config)
    except pytest.UsageError as exc:
        pytest.skip(str(exc))
    task_id = config["task_id"]
    log_path = config["log_path"]
    agent_prompt_path = config["agent_prompt_path"]
    out_dir = config["out_dir"]
    judge_timeout_seconds = config["judge_timeout_seconds"]

    if not log_path.exists():
        pytest.skip(f"manual trajectory log not found: {log_path}")
    if not agent_prompt_path.exists():
        pytest.skip(f"manual trajectory evaluator agent prompt not found: {agent_prompt_path}")
    if not os.getenv("LLM_MODEL_NAME") or not (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")):
        pytest.skip("real trajectory judge requires LLM_MODEL_NAME and LLM_API_KEY/OPENAI_API_KEY")

    suite = EvalSuiteDef(
        suite_id="trajectory-log-manual-replay",
        cases=[
            EvalCaseDef(
                case_id=task_id,
                input={
                    "trajectory_log": str(log_path),
                    "task_id": task_id,
                    "judge_agent_prompt": str(agent_prompt_path),
                },
            )
        ],
        runtime_harness=TrajectoryLogReplayHarness(out_dir=out_dir),
        judge_schema=JudgeSchemaDef(output_model=TrajectoryEvalJudgeOutput),
        judge_backend=MarkdownAgentJudgeBackend(
            backend_id="trajectory-evaluator-agent-md",
            agent_markdown_path=agent_prompt_path,
            prompt_builder=_trajectory_judge_prompt,
            timeout_seconds=judge_timeout_seconds,
        ),
        outcome_scorers=(
            StateCheckGrader(
                metric_name="has_evidence",
                source="outcome",
                path=("evidence_blocks",),
                op=">",
                expected=0,
            ),
            StateCheckGrader(
                metric_name="agent_finished",
                source="outcome",
                path=("is_finished",),
                op="==",
                expected=True,
            ),
        ),
        gate_policy=GatePolicyDef(
            pass_all=(
                GateMetricCondition(metric_name="score", op=">=", threshold=70.0),
                GateMetricCondition(metric_name="A1_groundedness", op=">=", threshold=3),
                GateMetricCondition(metric_name="has_evidence", op="==", threshold=1.0),
                GateMetricCondition(metric_name="agent_finished", op="==", threshold=1.0),
            )
        ),
        metadata={
            "manual_replay": True,
            "judge_agent_prompt": str(agent_prompt_path),
            "trajectory_log": str(log_path),
        },
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(
            target={"kind": "inline", "value": {"target_path": str(log_path), "target_kind": "trajectory_log"}},
            suite=suite,
        )
    )

    report_dict = report.to_dict()
    validate_evaluator_report(report_dict)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"evaluator_report_{task_id}.json"
    report_path.write_text(json.dumps(report_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    assert report["gate"]["status"] in {"pass", "fail", "needs_approval"}
    assert report["metrics"]["has_evidence"]["mean"] == 1.0
    assert report["metrics"]["agent_finished"]["mean"] == 1.0
    assert report["judge_backend"]["backend_id"] == "trajectory-evaluator-agent-md"
    assert report["results"][0]["judge"]["verdict"] in {"Excellent", "Pass", "Marginal", "Fail"}
    assert 0 <= report["results"][0]["judge"]["score"] <= 100
    assert report["results"][0]["state_summary"]["answer"]
    assert Path(report["results"][0]["metadata"]["extracted_path"]).exists()
    _assert_report_trajectory_steps_match_extracted(report["results"][0])
    assert report_path.exists()
