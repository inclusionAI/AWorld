from __future__ import annotations

import pytest
from pydantic import BaseModel

from aworld.evaluations.base import EvalCriteria
from aworld.evaluations.runtime_composition import (
    CallableRuntimeHarness,
    RetryRuntimeHarness,
    RolloutState,
    RolloutTurn,
    ScriptedUserSimulator,
    SinglePromptUserSimulator,
    StateCheckGrader,
    StepReward,
    aggregate_step_rewards,
    derive_standard_metrics,
)
from aworld.evaluations.scorers import scorer_factory
from aworld.evaluations.substrate import (
    EvalCaseDef,
    EvalSuiteDef,
    EvaluationFlowDef,
    GateMetricCondition,
    GatePolicyDef,
    JudgeSchemaDef,
    TrajectoryScorerDef,
    get_builtin_eval_suite,
    run_evaluation_flow,
)
from aworld.evaluations.types import MetricNames


class RuntimeJudgeOutput(BaseModel):
    score: float
    verdict: str


def test_rollout_state_to_eval_state_excludes_live_handles():
    live_agent = object()
    state = RolloutState(
        case_id="case-1",
        status="success",
        answer="done",
        turns=[RolloutTurn(role="user", content="hello")],
        outcome={"artifact_exists": True},
        metadata={"live_agent": live_agent, "safe": "ok"},
    )

    eval_state = state.to_eval_state(target={"target_kind": "inline"})

    assert eval_state.case_id == "case-1"
    assert eval_state.answer == "done"
    assert eval_state.trajectory
    assert eval_state.artifacts["outcome"]["artifact_exists"] is True
    assert "live_agent" not in eval_state.metadata
    assert eval_state.metadata["safe"] == "ok"


def test_state_check_grader_emits_outcome_metric():
    state = RolloutState(
        case_id="case-1",
        status="success",
        outcome={"ticket": {"status": "resolved"}},
    )
    grader = StateCheckGrader(
        metric_name="ticket_resolved",
        path=("ticket", "status"),
        expected="resolved",
    )

    result = grader.grade(state=state, case=None, target={})

    assert result.metric_name == "ticket_resolved"
    assert result.value == 1.0
    assert result.passed is True


def test_state_check_grader_fails_non_numeric_comparison_without_crashing():
    state = RolloutState(
        case_id="case-1",
        status="success",
        outcome={"latency_ms": "not-a-number"},
    )
    grader = StateCheckGrader(
        metric_name="latency_ok",
        path=("latency_ms",),
        op="<=",
        expected=1000,
    )

    result = grader.grade(state=state, case=None, target={})

    assert result.metric_name == "latency_ok"
    assert result.value == 0.0
    assert result.passed is False
    assert "not comparable" in result.reason
    assert result.metadata["actual"] == "not-a-number"


def test_state_check_grader_rejects_unsupported_operator():
    state = RolloutState(
        case_id="case-1",
        status="success",
        outcome={"latency_ms": 10},
    )
    grader = StateCheckGrader(
        metric_name="latency_ok",
        path=("latency_ms",),
        op="between",
        expected=1000,
    )

    with pytest.raises(ValueError, match="unsupported state-check operator"):
        grader.grade(state=state, case=None, target={})


def test_scripted_user_simulator_emits_turns_in_order():
    simulator = ScriptedUserSimulator()
    state = RolloutState(case_id="case-1")
    case = EvalCaseDef(case_id="case-1", input={"turns": ["hi", "again"]})

    first = simulator.next_turn(case=case, target={}, state=state, last_output=None)
    state.turns.append(first)
    second = simulator.next_turn(case=case, target={}, state=state, last_output="ok")

    assert first.content == "hi"
    assert second.content == "again"


def test_single_prompt_user_simulator_emits_one_turn():
    simulator = SinglePromptUserSimulator()
    case = EvalCaseDef(case_id="case-1", input={"query": "hello"})
    state = RolloutState(case_id="case-1")

    first = simulator.next_turn(case=case, target={}, state=state)
    state.turns.append(first)
    second = simulator.next_turn(case=case, target={}, state=state)

    assert first.content == "hello"
    assert second is None


@pytest.mark.asyncio
async def test_runtime_harness_executes_multi_turn_rollout():
    async def assistant_step(*, user_turn, state, case, target):
        return {
            "answer": f"ack:{user_turn.content}",
            "tool_calls": [{"id": f"call-{len(state.turns)}"}],
        }

    harness = CallableRuntimeHarness(
        simulator=ScriptedUserSimulator(),
        assistant_step=assistant_step,
        max_turns=2,
    )
    case = EvalCaseDef(case_id="case-1", input={"turns": ["hi", "again"]})

    state = await harness.run_rollout(case=case, target={"target_kind": "inline"})

    assert state.answer == "ack:again"
    assert [turn.role for turn in state.turns] == ["user", "assistant", "user", "assistant"]
    assert len(state.trajectory) == 4
    assert state.tool_calls == [{"id": "call-1"}, {"id": "call-3"}]


def test_step_rewards_aggregate_into_metrics():
    state = RolloutState(
        case_id="case-1",
        step_rewards=[
            StepReward(metric_name="process_quality", step_index=0, value=1.0, weight=2.0),
            StepReward(
                metric_name="process_quality",
                step_index=1,
                value=0.5,
                weight=1.0,
                partial_credit=True,
            ),
        ],
    )

    metrics = aggregate_step_rewards(state)

    assert metrics["process_quality"]["value"] == pytest.approx((1.0 * 2.0 + 0.5) / 3.0)
    assert metrics["process_quality_total"]["value"] == pytest.approx(1.5)
    assert metrics["process_quality_partial_credit_rate"]["value"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_retry_wrapper_preserves_failed_attempts():
    attempts = []

    class FlakyHarness:
        async def run_rollout(self, *, case, target):
            attempts.append(len(attempts) + 1)
            if len(attempts) == 1:
                return RolloutState(case_id=case.case_id, status="failed", answer="bad")
            return RolloutState(case_id=case.case_id, status="success", answer="ok")

    wrapper = RetryRuntimeHarness(base_harness=FlakyHarness(), max_attempts=2)
    case = EvalCaseDef(case_id="case-1", input={"query": "hello"})

    state = await wrapper.run_rollout(case=case, target={})

    assert state.status == "success"
    assert state.answer == "ok"
    assert [attempt.status for attempt in state.attempts] == ["failed", "success"]
    assert "pass@1" not in state.standard_metrics
    assert "pass^1" not in state.standard_metrics


@pytest.mark.asyncio
async def test_retry_wrapper_attempts_serialize_without_self_recursion():
    class FlakyHarness:
        def __init__(self):
            self.calls = 0

        async def run_rollout(self, *, case, target):
            self.calls += 1
            return RolloutState(
                case_id=case.case_id,
                status="success" if self.calls == 2 else "failed",
                answer=f"attempt-{self.calls}",
            )

    wrapper = RetryRuntimeHarness(base_harness=FlakyHarness(), max_attempts=2)
    case = EvalCaseDef(case_id="case-1", input={"query": "hello"})

    state = await wrapper.run_rollout(case=case, target={})
    eval_state = state.to_eval_state(target={})
    state_dict = state.to_dict()

    assert [attempt["answer"] for attempt in eval_state.artifacts["attempts"]] == ["attempt-1", "attempt-2"]
    assert [attempt["answer"] for attempt in state_dict["attempts"]] == ["attempt-1", "attempt-2"]


def test_rollout_standard_metrics_are_derived():
    state = RolloutState(
        case_id="case-1",
        turns=[
            RolloutTurn(role="user", content="hello"),
            RolloutTurn(role="assistant", content="ok"),
        ],
        tool_calls=[{"id": "call-1"}],
        usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        timing={"duration_ms": 120},
    )

    metrics = derive_standard_metrics(state)

    assert metrics == {
        "n_turns": 2,
        "n_tool_calls": 1,
        "n_tokens": 5,
        "duration_ms": 120,
    }


@pytest.mark.asyncio
async def test_runtime_composition_adoption_suite_runs_end_to_end():
    async def assistant_step(*, user_turn, state, case, target):
        return {
            "answer": "ticket resolved",
            "outcome": {"ticket": {"status": "resolved"}},
            "step_rewards": [
                StepReward(metric_name="process_quality", step_index=0, value=1.0, reason="direct resolution")
            ],
            "tool_calls": [{"id": "call-1", "function": {"name": "resolve_ticket", "arguments": "{}"}}],
            "usage": {"total_tokens": 7},
            "timing": {"duration_ms": 25},
        }

    async def fake_judge(case_input, target):
        assert target["artifacts"]["outcome"]["ticket"]["status"] == "resolved"
        return {"score": 1.0, "verdict": "approved"}

    suite = EvalSuiteDef(
        suite_id="runtime-adoption",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "resolve ticket"})],
        runtime_harness=CallableRuntimeHarness(
            simulator=SinglePromptUserSimulator(),
            assistant_step=assistant_step,
            max_turns=1,
        ),
        judge_schema=JudgeSchemaDef(output_model=RuntimeJudgeOutput),
        judge=fake_judge,
        outcome_scorers=(
            StateCheckGrader(
                metric_name="ticket_resolved",
                path=("ticket", "status"),
                expected="resolved",
            ),
        ),
        reward_metrics=("process_quality",),
        standard_metrics=("n_turns", "n_tool_calls", "n_tokens", "duration_ms"),
        trajectory_scorers=(
            TrajectoryScorerDef(metric_name=MetricNames.TRAJECTORY_TOOL_CALLS, threshold=1.0),
        ),
        gate_policy=GatePolicyDef(
            pass_all=(
                GateMetricCondition(metric_name="score", op=">=", threshold=0.9),
                GateMetricCondition(metric_name="ticket_resolved", op="==", threshold=1.0),
                GateMetricCondition(metric_name="process_quality", op=">=", threshold=1.0),
                GateMetricCondition(metric_name="n_turns", op="==", threshold=2),
                GateMetricCondition(metric_name=MetricNames.TRAJECTORY_TOOL_CALLS, op="==", threshold=1.0),
            )
        ),
        metadata={"evaluation_purpose": "capability"},
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(target={"kind": "inline", "value": {"target_path": "demo"}}, suite=suite)
    )

    assert report["gate"]["status"] == "pass"
    assert report["metrics"]["ticket_resolved"]["mean"] == pytest.approx(1.0)
    assert report["metrics"]["process_quality"]["mean"] == pytest.approx(1.0)
    assert report["metrics"]["n_turns"]["mean"] == pytest.approx(2.0)
    assert report["results"][0]["metric_details"]["ticket_resolved"]["passed"] is True
    assert report["results"][0]["artifacts"]["outcome"]["ticket"]["status"] == "resolved"
    assert report["suite_metadata"]["evaluation_purpose"] == "capability"


def test_builtin_runtime_composition_adoption_suite_is_registered():
    suite = get_builtin_eval_suite("runtime-composition-adoption")

    assert suite.suite_id == "runtime-composition-adoption"
    assert suite.runtime_harness is not None
    assert suite.judge_schema.output_model is not None
    assert suite.outcome_scorers
    assert suite.reward_metrics == ("process_quality",)
    assert suite.metadata["evaluation_purpose"] == "capability"


def test_runtime_scorer_can_be_selected_by_full_class_name_for_dynamic_metric():
    scorers = scorer_factory(
        criterias=[
            EvalCriteria(
                metric_name="custom_outcome",
                scorer_class="aworld.evaluations.scorers.runtime_composition.RuntimeOutcomeScorer",
                scorer_params={
                    "grader": {
                        "path": ["ok"],
                        "expected": True,
                    }
                },
            )
        ]
    )

    assert scorers[0].__class__.__name__ == "RuntimeOutcomeScorer"
