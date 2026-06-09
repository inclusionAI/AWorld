# coding: utf-8
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Protocol

from aworld.core.task import TaskResponse
from aworld.evaluations.execution import (
    EvalExecutionMode,
    EvalExecutionSpec,
    EvalState,
    _validate_importable_callable_ref,
    load_program_callable,
    normalize_task_response_to_eval_state,
)
from aworld.runner import Runners


class ExecutionAdapter(Protocol):
    async def execute(self, *, case: Any, target: dict[str, Any], spec: EvalExecutionSpec) -> EvalState:
        raise NotImplementedError


@dataclass(frozen=True)
class StaticExecutionAdapter:
    async def execute(self, *, case: Any, target: dict[str, Any], spec: EvalExecutionSpec) -> EvalState:
        return EvalState(
            case_id=case.case_id,
            status="not_evaluated",
            metadata={"_target": dict(target)},
        )


@dataclass(frozen=True)
class AgentExecutionAdapter:
    async def execute(self, *, case: Any, target: dict[str, Any], spec: EvalExecutionSpec) -> EvalState:
        query_column = spec.query_column or "query"
        query = case.input[query_column]
        if "agent" not in spec.target_config:
            raise ValueError("agent execution requires target_config['agent']")
        response = await Runners.run(query, agent=spec.target_config["agent"])
        return normalize_task_response_to_eval_state(
            case_id=case.case_id,
            response=response,
            target=target,
            metadata=case.input,
        )


@dataclass(frozen=True)
class TaskExecutionAdapter:
    async def execute(self, *, case: Any, target: dict[str, Any], spec: EvalExecutionSpec) -> EvalState:
        task = spec.target_config.get("task")
        if task is None:
            if not spec.task_builder_ref:
                raise ValueError("task execution requires task_builder_ref")
            builder = load_program_callable(spec.task_builder_ref)
            task = builder(case=case, target=target, spec=spec)
            if inspect.isawaitable(task):
                task = await task

        result = await Runners.run_task(task=task)
        if isinstance(result, dict) and getattr(task, "id", None) in result:
            result = result[task.id]
        elif isinstance(result, dict) and len(result) == 1 and not {"status", "answer", "completion"} & result.keys():
            result = next(iter(result.values()))
        elif isinstance(result, TaskResponse):
            result = result

        return normalize_task_response_to_eval_state(
            case_id=case.case_id,
            response=result,
            target=target,
            metadata=case.input,
        )


@dataclass(frozen=True)
class ProgramExecutionAdapter:
    async def execute(self, *, case: Any, target: dict[str, Any], spec: EvalExecutionSpec) -> EvalState:
        if not spec.target_ref:
            raise ValueError("program execution requires target_ref")
        program = load_program_callable(spec.target_ref)
        result = program(case, spec, target)
        if inspect.isawaitable(result):
            result = await result
        return normalize_task_response_to_eval_state(
            case_id=case.case_id,
            response=result,
            target=target,
            metadata={**case.input, "_execution_mode": spec.mode.value},
        )


def _validate_program_execution_spec(spec: EvalExecutionSpec) -> None:
    if not spec.target_ref:
        raise ValueError("program execution requires target_ref")
    _validate_importable_callable_ref(spec.target_ref)
    unsupported_config_keys = {"command", "commands", "workflow", "workflow_engine", "sandbox"}
    if spec.runner_method is not None or unsupported_config_keys & set(spec.target_config):
        raise ValueError("unsupported program execution configuration")


def resolve_execution_adapter(spec: EvalExecutionSpec) -> ExecutionAdapter:
    if spec.mode == EvalExecutionMode.STATIC:
        return StaticExecutionAdapter()
    if spec.mode == EvalExecutionMode.AGENT:
        return AgentExecutionAdapter()
    if spec.mode == EvalExecutionMode.TASK:
        return TaskExecutionAdapter()
    if spec.mode == EvalExecutionMode.PROGRAM:
        _validate_program_execution_spec(spec)
        return ProgramExecutionAdapter()
    raise ValueError(f"unsupported execution mode: {spec.mode}")
