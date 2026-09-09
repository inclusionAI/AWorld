from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from aworld.config.conf import SelfEvolveJudgeConfig
from aworld.self_evolve.credit_assignment import TargetSelectionReport
from aworld.self_evolve.trace_pack import TracePack
from aworld.self_evolve.types import EvaluationSummary


JudgeCallable = Callable[[Mapping[str, Any]], Any]
AgentMdLoader = Callable[[Path], JudgeCallable]


@dataclass(frozen=True)
class JudgeInput:
    trace_pack: TracePack
    target_selection: TargetSelectionReport
    baseline: EvaluationSummary
    candidate: EvaluationSummary
    scorer_diagnostics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JudgeVerdict:
    score: float
    verdict: str
    rationale: str | None = None
    confidence: str = "limited"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JudgeRecord:
    backend_id: str
    prompt: str
    compact_input: Mapping[str, Any]
    output: Mapping[str, Any]
    verdict: JudgeVerdict


class JudgeBackend(Protocol):
    async def judge(self, judge_input: JudgeInput) -> JudgeRecord:
        """Run a judge as an evaluation signal."""


class DefaultTrajectoryJudgeBackend:
    backend_id = "default_trajectory"

    async def judge(self, judge_input: JudgeInput) -> JudgeRecord:
        compact_input = compact_judge_input(judge_input)
        baseline_score = _numeric_metric(judge_input.baseline.metrics, "score")
        candidate_score = _numeric_metric(judge_input.candidate.metrics, "score")
        command_pass_rate = _numeric_metric(
            judge_input.candidate.metrics,
            "command_pass_rate",
        )
        improved = (
            candidate_score is not None
            and baseline_score is not None
            and candidate_score > baseline_score
        )
        score = 1.0 if improved else 0.0
        output = {
            "score": score,
            "verdict": "candidate_improves" if improved else "no_clear_improvement",
            "rationale": "Compared compact trajectory evidence and variant metrics.",
            "metadata": {
                "judge_only_signal": command_pass_rate is None,
                "baseline_score": baseline_score,
                "candidate_score": candidate_score,
            },
        }
        return JudgeRecord(
            backend_id=self.backend_id,
            prompt=DEFAULT_TRAJECTORY_JUDGE_PROMPT,
            compact_input=compact_input,
            output=output,
            verdict=_verdict_from_output(output),
        )


class DisabledJudgeBackend:
    backend_id = "disabled"

    async def judge(self, judge_input: JudgeInput) -> JudgeRecord:
        output = {
            "score": 0.0,
            "verdict": "disabled",
            "rationale": "LLM judge signals are disabled for this run.",
        }
        return JudgeRecord(
            backend_id=self.backend_id,
            prompt="",
            compact_input={},
            output=output,
            verdict=_verdict_from_output(output),
        )


class AgentMdJudgeBackend:
    backend_id = "agent_md"

    def __init__(self, *, agent_path: str | Path, loader: AgentMdLoader | None = None) -> None:
        self.agent_path = Path(agent_path)
        self.loader = loader or _default_agent_md_loader

    async def judge(self, judge_input: JudgeInput) -> JudgeRecord:
        compact_input = compact_judge_input(judge_input)
        agent = self.loader(self.agent_path)
        output = await _call_judge(agent, compact_input)
        return JudgeRecord(
            backend_id=self.backend_id,
            prompt=f"agent.md judge: {self.agent_path}",
            compact_input=compact_input,
            output=output,
            verdict=_verdict_from_output(output),
        )


class CustomAgentJudgeBackend:
    backend_id = "custom_agent"

    def __init__(self, *, agent_id: str, agent: JudgeCallable) -> None:
        self.agent_id = agent_id
        self.agent = agent

    async def judge(self, judge_input: JudgeInput) -> JudgeRecord:
        compact_input = compact_judge_input(judge_input)
        output = await _call_judge(self.agent, compact_input)
        output = {**output, "metadata": {"agent_id": self.agent_id, **output.get("metadata", {})}}
        return JudgeRecord(
            backend_id=self.backend_id,
            prompt=f"custom judge agent: {self.agent_id}",
            compact_input=compact_input,
            output=output,
            verdict=_verdict_from_output(output),
        )


DEFAULT_TRAJECTORY_JUDGE_PROMPT = (
    "Evaluate whether a self-evolve candidate improves the selected harness "
    "target using only compact trajectory evidence, target-selection context, "
    "variant metrics, and scorer diagnostics. Return a score, verdict, and "
    "rationale. Judge-only positive signals remain limited confidence."
)


def build_judge_backend(
    config: SelfEvolveJudgeConfig,
    *,
    agent_md_loader: AgentMdLoader | None = None,
    custom_agents: Mapping[str, JudgeCallable] | None = None,
) -> JudgeBackend:
    if config.mode == "trajectory":
        return DefaultTrajectoryJudgeBackend()
    if config.mode == "disabled":
        return DisabledJudgeBackend()
    if config.mode == "agent_md":
        if not config.agent_path:
            raise ValueError("agent_md judge requires agent_path")
        return AgentMdJudgeBackend(agent_path=config.agent_path, loader=agent_md_loader)
    if config.mode == "custom_agent":
        if not config.agent_id:
            raise ValueError("custom_agent judge requires agent_id")
        agents = custom_agents or {}
        if config.agent_id not in agents:
            raise ValueError(f"custom judge agent not found: {config.agent_id}")
        return CustomAgentJudgeBackend(agent_id=config.agent_id, agent=agents[config.agent_id])
    raise ValueError(f"unsupported judge mode: {config.mode}")


def compact_judge_input(judge_input: JudgeInput) -> Mapping[str, Any]:
    target = judge_input.target_selection.selected_target
    return {
        "trace_pack": {
            "pack_id": judge_input.trace_pack.pack_id,
            "task_id": judge_input.trace_pack.task_id,
            "source_kind": judge_input.trace_pack.source_kind,
            "evidence_step_ids": [
                step.evidence_id for step in judge_input.trace_pack.steps
            ],
            "final_action_excerpt": judge_input.trace_pack.final_action_excerpt,
            "compression_summary": judge_input.trace_pack.compression_summary,
        },
        "target_selection": {
            "target": (
                f"{target.target_type}:{target.target_id}"
                if target is not None
                else None
            ),
            "confidence": judge_input.target_selection.confidence,
            "evidence_step_ids": list(judge_input.target_selection.evidence_step_ids),
            "failure_category": judge_input.target_selection.failure_category,
            "signals": list(judge_input.target_selection.signals),
            "no_target_reason": judge_input.target_selection.no_target_reason,
        },
        "baseline": {
            "variant_id": judge_input.baseline.variant_id,
            "metrics": judge_input.baseline.metrics,
            "dataset_split": judge_input.baseline.dataset_split,
        },
        "candidate": {
            "variant_id": judge_input.candidate.variant_id,
            "metrics": judge_input.candidate.metrics,
            "dataset_split": judge_input.candidate.dataset_split,
        },
        "scorer_diagnostics": judge_input.scorer_diagnostics,
    }


async def _call_judge(agent: JudgeCallable, compact_input: Mapping[str, Any]) -> Mapping[str, Any]:
    output = agent(compact_input)
    if inspect.isawaitable(output):
        output = await output
    if not isinstance(output, Mapping):
        raise ValueError("judge output must be a mapping")
    return output


def _verdict_from_output(output: Mapping[str, Any]) -> JudgeVerdict:
    score = output.get("score", 0.0)
    return JudgeVerdict(
        score=float(score) if isinstance(score, (int, float)) else 0.0,
        verdict=str(output.get("verdict", "")),
        rationale=output.get("rationale") if isinstance(output.get("rationale"), str) else None,
        confidence=str(output.get("confidence", "limited")),
        metadata=output.get("metadata") if isinstance(output.get("metadata"), Mapping) else {},
    )


def _numeric_metric(metrics: Mapping[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _default_agent_md_loader(path: Path) -> JudgeCallable:
    try:
        from aworld_cli.core.markdown_agent_loader import parse_markdown_agent
    except ImportError as exc:
        raise ValueError("agent.md judge loading requires aworld-cli markdown loader") from exc
    agent = parse_markdown_agent(path)
    if callable(agent):
        return agent
    raise ValueError("loaded agent.md judge is not callable in this execution context")
