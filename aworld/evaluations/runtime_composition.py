# coding: utf-8
from __future__ import annotations

import inspect
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Protocol

from aworld.evaluations.execution import EvalExecutionSpec, EvalState


_SCALAR_TYPES = (str, int, float, bool, type(None))


def _is_serializable_value(value: Any) -> bool:
    if isinstance(value, _SCALAR_TYPES):
        return True
    if isinstance(value, list):
        return all(_is_serializable_value(item) for item in value)
    if isinstance(value, tuple):
        return all(_is_serializable_value(item) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _is_serializable_value(item) for key, item in value.items())
    return False


def _serializable_dict(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in dict(payload or {}).items()
        if isinstance(key, str) and _is_serializable_value(value)
    }


@dataclass(frozen=True)
class RolloutTurn:
    role: str
    content: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "role": self.role,
            "content": self.content,
        }
        metadata = _serializable_dict(self.metadata)
        if metadata:
            payload["metadata"] = metadata
        return payload


@dataclass(frozen=True)
class OutcomeCheckResult:
    metric_name: str
    value: float
    passed: bool
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_metric_result(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "metadata": {
                "passed": self.passed,
                "reason": self.reason,
                **_serializable_dict(self.metadata),
            },
        }


@dataclass(frozen=True)
class StepReward:
    metric_name: str
    step_index: int
    value: float
    weight: float = 1.0
    partial_credit: bool = False
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "step_index": self.step_index,
            "value": self.value,
            "weight": self.weight,
            "partial_credit": self.partial_credit,
            "reason": self.reason,
            "metadata": _serializable_dict(self.metadata),
        }


@dataclass(frozen=True)
class EnvironmentSnapshot:
    environment_id: str
    trial_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment_id": self.environment_id,
            "trial_id": self.trial_id,
            "metadata": _serializable_dict(self.metadata),
        }


def _resolve_path(source: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = source
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(".".join(path))
        current = current[part]
    return current


def _compare_values(value: Any, op: str, expected: Any) -> bool:
    if op == "==":
        return value == expected
    if op == "!=":
        return value != expected
    if op == ">=":
        return float(value) >= float(expected)
    if op == "<=":
        return float(value) <= float(expected)
    if op == ">":
        return float(value) > float(expected)
    if op == "<":
        return float(value) < float(expected)
    raise ValueError(f"unsupported state-check operator: {op}")


@dataclass(frozen=True)
class StateCheckGrader:
    metric_name: str
    path: tuple[str, ...]
    expected: Any
    source: str = "outcome"
    op: str = "=="
    weight: float = 1.0
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "source": self.source,
            "path": list(self.path),
            "op": self.op,
            "expected": self.expected,
            "weight": self.weight,
            "required": self.required,
        }

    def grade(self, *, state: RolloutState, case: Any, target: Mapping[str, Any]) -> OutcomeCheckResult:
        sources = {
            "outcome": state.outcome,
            "metadata": state.metadata,
            "artifacts": state.to_eval_state(target=target).artifacts,
        }
        if self.source not in sources:
            raise ValueError(f"unsupported state-check source: {self.source}")
        try:
            actual = _resolve_path(sources[self.source], self.path)
        except KeyError:
            actual = None
            passed = False
            reason = f"missing path: {'.'.join(self.path)}"
        else:
            try:
                passed = _compare_values(actual, self.op, self.expected)
            except (TypeError, ValueError) as exc:
                if isinstance(exc, ValueError) and str(exc).startswith("unsupported state-check operator"):
                    raise
                passed = False
                reason = f"not comparable: expected {self.expected!r}, got {actual!r} ({exc})"
            else:
                reason = "matched" if passed else f"expected {self.expected!r}, got {actual!r}"
        return OutcomeCheckResult(
            metric_name=self.metric_name,
            value=1.0 if passed else 0.0,
            passed=passed,
            reason=reason,
            metadata={
                "source": self.source,
                "path": list(self.path),
                "op": self.op,
                "expected": self.expected,
                "actual": actual,
                "weight": self.weight,
                "required": self.required,
            },
        )


@dataclass
class RolloutState:
    case_id: str
    status: str = "success"
    answer: Any | None = None
    turns: list[RolloutTurn] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    trajectory: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    step_rewards: list[StepReward] = field(default_factory=list)
    outcome: dict[str, Any] = field(default_factory=dict)
    attempts: list["RolloutState"] = field(default_factory=list)
    child_states: list["RolloutState"] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, Any] = field(default_factory=dict)
    standard_metrics: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_eval_state(self, target: Mapping[str, Any] | None = None) -> EvalState:
        trajectory = list(self.trajectory)
        if not trajectory:
            trajectory = [turn.to_dict() for turn in self.turns]
        artifacts = {
            "outcome": _serializable_dict(self.outcome),
            "attempts": [attempt.to_dict(include_children=False) for attempt in self.attempts],
            "child_states": [state.to_dict(include_children=False) for state in self.child_states],
        }
        metadata = _serializable_dict(self.metadata)
        metadata["_target"] = dict(target or {})
        if self.standard_metrics:
            metadata["standard_metrics"] = _serializable_dict(self.standard_metrics)
        return EvalState(
            case_id=self.case_id,
            status=self.status,
            answer=self.answer,
            completion=[] if self.answer is None else [self.answer],
            artifacts=artifacts,
            trajectory=trajectory,
            tool_calls=list(self.tool_calls),
            usage=_serializable_dict(self.usage),
            timing=_serializable_dict(self.timing),
            error=self.error,
            raw_response=self.to_dict(include_children=False),
            metadata=metadata,
        )

    def to_dict(self, *, include_children: bool = True) -> dict[str, Any]:
        payload = {
            "case_id": self.case_id,
            "status": self.status,
            "answer": self.answer,
            "turns": [turn.to_dict() for turn in self.turns],
            "messages": list(self.messages),
            "trajectory": list(self.trajectory),
            "tool_calls": list(self.tool_calls),
            "step_rewards": [reward.to_dict() for reward in self.step_rewards],
            "outcome": _serializable_dict(self.outcome),
            "usage": _serializable_dict(self.usage),
            "timing": _serializable_dict(self.timing),
            "standard_metrics": _serializable_dict(self.standard_metrics),
            "error": self.error,
            "metadata": _serializable_dict(self.metadata),
        }
        if include_children:
            payload["attempts"] = [attempt.to_dict(include_children=False) for attempt in self.attempts]
            payload["child_states"] = [state.to_dict(include_children=False) for state in self.child_states]
        return payload


@dataclass(frozen=True)
class EvalRuntimeHarnessDef:
    harness_id: str
    execution: EvalExecutionSpec = field(default_factory=EvalExecutionSpec)
    simulator: str = "single_prompt"
    metadata: dict[str, Any] = field(default_factory=dict)


class RuntimeHarness(Protocol):
    async def run_rollout(self, *, case: Any, target: Mapping[str, Any]) -> RolloutState:
        ...


class EnvironmentFixture(Protocol):
    def reset(self, *, case: Any, target: Mapping[str, Any]) -> EnvironmentSnapshot | Mapping[str, Any]:
        ...

    def cleanup(
        self,
        *,
        snapshot: EnvironmentSnapshot,
        case: Any,
        target: Mapping[str, Any],
        state: RolloutState,
    ) -> EnvironmentSnapshot | Mapping[str, Any] | None:
        ...


class UserSimulator(Protocol):
    def next_turn(
        self,
        *,
        case: Any,
        target: Mapping[str, Any],
        state: RolloutState,
        last_output: Any | None = None,
    ) -> RolloutTurn | None:
        ...


def _case_input(case: Any) -> dict[str, Any]:
    if hasattr(case, "input") and isinstance(case.input, Mapping):
        return dict(case.input)
    if hasattr(case, "case_data") and isinstance(case.case_data, Mapping):
        return dict(case.case_data)
    if isinstance(case, Mapping):
        return dict(case)
    return {}


class ScriptedUserSimulator:
    def next_turn(
        self,
        *,
        case: Any,
        target: Mapping[str, Any],
        state: RolloutState,
        last_output: Any | None = None,
    ) -> RolloutTurn | None:
        turns = _case_input(case).get("turns") or []
        user_turn_count = sum(1 for turn in state.turns if turn.role == "user")
        if user_turn_count >= len(turns):
            return None
        return RolloutTurn(role="user", content=turns[user_turn_count])


class SinglePromptUserSimulator:
    def __init__(self, query_key: str = "query"):
        self.query_key = query_key

    def next_turn(
        self,
        *,
        case: Any,
        target: Mapping[str, Any],
        state: RolloutState,
        last_output: Any | None = None,
    ) -> RolloutTurn | None:
        if any(turn.role == "user" for turn in state.turns):
            return None
        case_input = _case_input(case)
        content = case_input.get(self.query_key, case_input.get("prompt"))
        if content is None:
            return None
        return RolloutTurn(role="user", content=content)


class LLMUserSimulator:
    def __init__(self, *, turn_generator: Callable[..., Any]):
        self.turn_generator = turn_generator

    def next_turn(
        self,
        *,
        case: Any,
        target: Mapping[str, Any],
        state: RolloutState,
        last_output: Any | None = None,
    ) -> RolloutTurn | None | Any:
        user_turn_count = sum(1 for turn in state.turns if turn.role == "user")
        generated = self.turn_generator(
            case=case,
            target=target,
            state=state,
            last_output=last_output,
            turn_index=user_turn_count,
        )
        if inspect.isawaitable(generated):
            return self._await_turn(generated)
        return self._normalize_turn(generated)

    async def _await_turn(self, generated: Any) -> RolloutTurn | None:
        return self._normalize_turn(await generated)

    def _normalize_turn(self, generated: Any) -> RolloutTurn | None:
        if generated is None:
            return None
        if isinstance(generated, RolloutTurn):
            return generated
        if isinstance(generated, str):
            return RolloutTurn(role="user", content=generated)
        if isinstance(generated, Mapping):
            if generated.get("stop") is True:
                return None
            return RolloutTurn(
                role=str(generated.get("role", "user")),
                content=generated.get("content"),
                metadata=dict(generated.get("metadata") or {}),
            )
        raise TypeError("LLMUserSimulator generator must return str, mapping, RolloutTurn, or None")


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _environment_snapshot_from(value: EnvironmentSnapshot | Mapping[str, Any], *, case: Any) -> EnvironmentSnapshot:
    if isinstance(value, EnvironmentSnapshot):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("environment fixture must return EnvironmentSnapshot or mapping")
    environment_id = value.get("environment_id")
    if environment_id is None:
        raise ValueError("environment snapshot requires environment_id")
    case_input = _case_input(case)
    trial = case_input.get("_trial") if isinstance(case_input.get("_trial"), Mapping) else {}
    return EnvironmentSnapshot(
        environment_id=str(environment_id),
        trial_id=value.get("trial_id") or trial.get("trial_id"),
        metadata=dict(value.get("metadata") or {}),
    )


def _case_with_environment(case: Any, snapshot: EnvironmentSnapshot) -> Any:
    snapshot_dict = snapshot.to_dict()
    case_input = _case_input(case)
    metadata = getattr(case, "metadata", {})
    if not isinstance(metadata, Mapping):
        metadata = {}
    try:
        return replace(
            case,
            input={**case_input, "_environment": snapshot_dict},
            metadata={**dict(metadata), "_environment": snapshot_dict},
        )
    except TypeError:
        return case


class EnvironmentIsolatedRuntimeHarness:
    def __init__(self, *, base_harness: RuntimeHarness, fixture: EnvironmentFixture):
        self.base_harness = base_harness
        self.fixture = fixture

    async def run_rollout(self, *, case: Any, target: Mapping[str, Any]) -> RolloutState:
        reset_value = await _maybe_await(self.fixture.reset(case=case, target=target))
        snapshot = _environment_snapshot_from(reset_value, case=case)
        snapshot_dict = snapshot.to_dict()
        isolated_case = _case_with_environment(case, snapshot)
        isolated_target = {**dict(target), "_environment": snapshot_dict}

        try:
            state = await self.base_harness.run_rollout(case=isolated_case, target=isolated_target)
        except Exception:
            cleanup_state = RolloutState(case_id=str(getattr(case, "case_id", "case")), status="failed")
            try:
                await _maybe_await(
                    self.fixture.cleanup(
                        snapshot=snapshot,
                        case=isolated_case,
                        target=isolated_target,
                        state=cleanup_state,
                    )
                )
            except Exception:
                pass
            raise

        state.metadata = {
            **state.metadata,
            "environment": snapshot_dict,
        }
        try:
            cleanup_value = await _maybe_await(
                self.fixture.cleanup(
                    snapshot=snapshot,
                    case=isolated_case,
                    target=isolated_target,
                    state=state,
                )
            )
        except Exception as exc:
            state.status = "failed"
            state.error = {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "phase": "environment_cleanup",
            }
            state.metadata = {
                **state.metadata,
                "environment_cleanup_error": dict(state.error),
            }
            return state

        if cleanup_value is not None:
            cleanup_snapshot = _environment_snapshot_from(cleanup_value, case=isolated_case)
            state.metadata = {
                **state.metadata,
                "environment_cleanup": cleanup_snapshot.to_dict(),
            }
        return state


class CallableRuntimeHarness:
    def __init__(
        self,
        *,
        simulator: UserSimulator | None = None,
        assistant_step: Callable[..., Any],
        max_turns: int = 1,
    ):
        self.simulator = simulator or SinglePromptUserSimulator()
        self.assistant_step = assistant_step
        self.max_turns = max_turns

    async def run_rollout(self, *, case: Any, target: Mapping[str, Any]) -> RolloutState:
        case_id = getattr(case, "case_id", None) or getattr(case, "eval_case_id", "case")
        state = RolloutState(case_id=str(case_id))
        last_output: Any | None = None
        for _ in range(self.max_turns):
            user_turn = await _maybe_await(
                self.simulator.next_turn(
                    case=case,
                    target=target,
                    state=state,
                    last_output=last_output,
                )
            )
            if user_turn is None:
                break
            state.turns.append(user_turn)
            state.trajectory.append(user_turn.to_dict())
            step_output = await _maybe_await(
                self.assistant_step(
                    user_turn=user_turn,
                    state=state,
                    case=case,
                    target=target,
                )
            )
            assistant_turn = self._assistant_turn(step_output)
            state.turns.append(assistant_turn)
            state.trajectory.append(assistant_turn.to_dict())
            if isinstance(step_output, Mapping):
                if "answer" in step_output:
                    state.answer = step_output["answer"]
                    last_output = step_output["answer"]
                for call in step_output.get("tool_calls") or []:
                    if isinstance(call, Mapping):
                        state.tool_calls.append(dict(call))
                if isinstance(step_output.get("outcome"), Mapping):
                    state.outcome.update(dict(step_output["outcome"]))
                if isinstance(step_output.get("usage"), Mapping):
                    state.usage.update(dict(step_output["usage"]))
                if isinstance(step_output.get("timing"), Mapping):
                    state.timing.update(dict(step_output["timing"]))
                for reward in step_output.get("step_rewards") or []:
                    if isinstance(reward, StepReward):
                        state.step_rewards.append(reward)
                    elif isinstance(reward, Mapping):
                        state.step_rewards.append(
                            StepReward(
                                metric_name=str(reward["metric_name"]),
                                step_index=int(reward["step_index"]),
                                value=float(reward["value"]),
                                weight=float(reward.get("weight", 1.0)),
                                partial_credit=bool(reward.get("partial_credit", False)),
                                reason=str(reward.get("reason", "")),
                                metadata=dict(reward.get("metadata") or {}),
                            )
                        )
            else:
                state.answer = step_output
                last_output = step_output
        state.standard_metrics.update(derive_standard_metrics(state))
        return state

    def _assistant_turn(self, step_output: Any) -> RolloutTurn:
        if isinstance(step_output, Mapping):
            return RolloutTurn(
                role="assistant",
                content=step_output.get("answer"),
                metadata={
                    "tool_calls": list(step_output.get("tool_calls") or []),
                },
            )
        return RolloutTurn(role="assistant", content=step_output)


def derive_standard_metrics(state: RolloutState) -> dict[str, Any]:
    token_total = state.usage.get("total_tokens")
    if token_total is None and isinstance(state.usage.get("tokens"), (int, float)):
        token_total = state.usage["tokens"]
    duration = state.timing.get("duration_ms", state.timing.get("time_cost_ms"))
    return {
        "n_turns": len(state.turns),
        "n_tool_calls": len(state.tool_calls),
        "n_tokens": token_total or 0,
        "duration_ms": duration or 0,
    }


def aggregate_step_rewards(state: RolloutState) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[StepReward]] = {}
    for reward in state.step_rewards:
        grouped.setdefault(reward.metric_name, []).append(reward)

    metrics: dict[str, dict[str, Any]] = {}
    for metric_name, rewards in grouped.items():
        weighted_sum = sum(float(reward.value) * float(reward.weight) for reward in rewards)
        weight_total = sum(float(reward.weight) for reward in rewards) or 1.0
        total = sum(float(reward.value) for reward in rewards)
        partial_count = sum(1 for reward in rewards if reward.partial_credit)
        metrics[metric_name] = {
            "value": weighted_sum / weight_total,
            "metadata": {
                "count": len(rewards),
                "weight_total": weight_total,
                "rewards": [reward.to_dict() for reward in rewards],
            },
        }
        metrics[f"{metric_name}_total"] = {
            "value": total,
            "metadata": {"count": len(rewards)},
        }
        metrics[f"{metric_name}_partial_credit_rate"] = {
            "value": partial_count / len(rewards),
            "metadata": {"partial_credit_count": partial_count, "count": len(rewards)},
        }
    return metrics


class RetryRuntimeHarness:
    def __init__(self, *, base_harness: RuntimeHarness, max_attempts: int = 2):
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.base_harness = base_harness
        self.max_attempts = max_attempts

    async def run_rollout(self, *, case: Any, target: Mapping[str, Any]) -> RolloutState:
        attempts: list[RolloutState] = []
        terminal: RolloutState | None = None
        for _ in range(self.max_attempts):
            attempt = await self.base_harness.run_rollout(case=case, target=target)
            attempts.append(attempt)
            terminal = attempt
            if attempt.status == "success":
                break
        assert terminal is not None
        terminal.attempts = attempts
        terminal.child_states = attempts[:-1]
        terminal.metadata = {
            **terminal.metadata,
            "runtime_composition": "retry",
            "attempt_count": len(attempts),
        }
        terminal.standard_metrics.update(derive_standard_metrics(terminal))
        return terminal
