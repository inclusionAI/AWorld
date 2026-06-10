# coding: utf-8
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol


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
class EvalSourceRecord:
    case_id: str
    input: Mapping[str, Any]
    expected: Any | None = None
    answer: Any | None = None
    state: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    raw_payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_raw_payload: bool = False) -> dict[str, Any]:
        payload = {
            "case_id": self.case_id,
            "input": _serializable_dict(self.input),
            "expected": self.expected,
            "answer": self.answer,
            "state": _serializable_dict(self.state),
            "metadata": _serializable_dict(self.metadata),
        }
        if include_raw_payload:
            payload["raw_payload"] = _serializable_dict(self.raw_payload)
        return {key: value for key, value in payload.items() if value not in (None, {}, [])}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvalSourceRecord":
        return cls(
            case_id=str(payload["case_id"]),
            input=dict(payload.get("input") or {}),
            expected=payload.get("expected"),
            answer=payload.get("answer"),
            state=dict(payload.get("state") or {}) if isinstance(payload.get("state"), Mapping) else None,
            metadata=dict(payload.get("metadata") or {}),
            raw_payload=dict(payload.get("raw_payload") or {}),
        )

    def to_case(self):
        from aworld.evaluations.substrate import EvalCaseDef

        return EvalCaseDef(
            case_id=self.case_id,
            input=dict(self.input),
            expected=self.expected,
            metadata={
                **dict(self.metadata or {}),
                "source_record": self.to_dict(),
            },
        )


class EvalSource(Protocol):
    def iter_records(self) -> Iterable[EvalSourceRecord]:
        ...

    def to_cases(self):
        ...

    def default_adapter(self):
        ...


class _BaseEvalSource:
    def to_cases(self):
        return tuple(record.to_case() for record in self.iter_records())


@dataclass(frozen=True)
class JsonlTaskAnswerSource(_BaseEvalSource):
    path: str | Path
    id_field: str = "id"
    input_field: str = "input"
    answer_field: str = "answer"
    expected_field: str | None = None
    metadata_field: str | None = None

    def iter_records(self) -> Iterable[EvalSourceRecord]:
        path = Path(self.path).expanduser()
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                payload = json.loads(stripped)
                if not isinstance(payload, Mapping):
                    raise ValueError(f"{path}:{line_number} must contain a JSON object")
                for field_name in (self.id_field, self.input_field, self.answer_field):
                    if field_name not in payload:
                        raise ValueError(f"{path}:{line_number} missing required field: {field_name}")
                metadata = {}
                if self.metadata_field is not None and isinstance(payload.get(self.metadata_field), Mapping):
                    metadata.update(dict(payload[self.metadata_field]))
                metadata.update({"source_kind": "answer", "source_path": str(path), "line_number": line_number})
                expected = payload.get(self.expected_field) if self.expected_field else None
                yield EvalSourceRecord(
                    case_id=str(payload[self.id_field]),
                    input={"input": payload[self.input_field]},
                    expected=expected,
                    answer=payload[self.answer_field],
                    metadata=metadata,
                    raw_payload=dict(payload),
                )

    def default_adapter(self):
        from aworld.evaluations.state_adapters import AnswerStateAdapter

        return AnswerStateAdapter()


@dataclass(frozen=True)
class JsonlTaskSource(_BaseEvalSource):
    path: str | Path
    id_field: str = "id"
    input_field: str = "input"
    expected_field: str | None = None
    metadata_field: str | None = None

    def iter_records(self) -> Iterable[EvalSourceRecord]:
        path = Path(self.path).expanduser()
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                payload = json.loads(stripped)
                if not isinstance(payload, Mapping):
                    raise ValueError(f"{path}:{line_number} must contain a JSON object")
                for field_name in (self.id_field, self.input_field):
                    if field_name not in payload:
                        raise ValueError(f"{path}:{line_number} missing required field: {field_name}")
                metadata = {}
                if self.metadata_field is not None and isinstance(payload.get(self.metadata_field), Mapping):
                    metadata.update(dict(payload[self.metadata_field]))
                metadata.update({"source_kind": "task", "source_path": str(path), "line_number": line_number})
                expected = payload.get(self.expected_field) if self.expected_field else None
                yield EvalSourceRecord(
                    case_id=str(payload[self.id_field]),
                    input={"input": payload[self.input_field]},
                    expected=expected,
                    metadata=metadata,
                    raw_payload=dict(payload),
                )

    def default_adapter(self):
        raise ValueError("task source requires a runtime_harness")


def _truthy_string(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _tool_calls_from_action(action: Mapping[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for tool_call in action.get("tool_calls") or []:
        if not isinstance(tool_call, Mapping):
            continue
        function = tool_call.get("function") or {}
        if isinstance(function, Mapping):
            calls.append({"name": function.get("name"), "arguments": str(function.get("arguments"))})
    return calls


def extract_aworld_trajectory_payload(
    trajectory: Iterable[Mapping[str, Any]],
    *,
    task_id: str,
    is_sub_task: Any | None = None,
) -> dict[str, Any]:
    trajectory = list(trajectory)
    if not isinstance(trajectory, list):
        raise ValueError(f"task_id {task_id} trajectory must be a list")

    question = None
    system_prompt = ""
    if trajectory:
        first_state = trajectory[0].get("state", {}) if isinstance(trajectory[0], Mapping) else {}
        question = (first_state.get("input", {}) or {}).get("content") if isinstance(first_state, Mapping) else None
        first_messages = first_state.get("messages", []) if isinstance(first_state, Mapping) else []
        if first_messages and isinstance(first_messages[0], Mapping) and first_messages[0].get("role") == "system":
            system_prompt = str(first_messages[0].get("content") or "")

    steps = []
    final_answer = None
    for item in trajectory:
        if not isinstance(item, Mapping):
            continue
        meta = item.get("meta", {}) if isinstance(item.get("meta"), Mapping) else {}
        action = item.get("action", {}) if isinstance(item.get("action"), Mapping) else {}
        finished = _truthy_string(action.get("is_agent_finished"))
        content = str(action.get("content") or "")
        steps.append(
            {
                "step": meta.get("step"),
                "pre_agent": meta.get("pre_agent"),
                "agent_id": meta.get("agent_id"),
                "tool_calls": _tool_calls_from_action(action),
                "assistant_content": content,
                "is_agent_finished": finished,
            }
        )
        if finished and content:
            final_answer = content

    final_messages = []
    if trajectory and isinstance(trajectory[-1], Mapping):
        final_state = trajectory[-1].get("state", {})
        if isinstance(final_state, Mapping):
            final_messages = final_state.get("messages", []) or []
    evidence = [
        {"msg_index": index, "content": str(message.get("content") or "")}
        for index, message in enumerate(final_messages)
        if isinstance(message, Mapping) and message.get("role") == "tool"
    ]

    return {
        "task_id": task_id,
        "is_sub_task": is_sub_task,
        "num_steps": len(trajectory),
        "question": question,
        "system_prompt_excerpt": system_prompt[:8000],
        "steps": steps,
        "final_answer": final_answer,
        "evidence": evidence,
    }


def _parse_aworld_trajectory_log_line(line: str) -> Mapping[str, Any]:
    clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
    record = ast.literal_eval(clean)
    if not isinstance(record, Mapping):
        raise ValueError("trajectory log line must contain a mapping")
    return record


def _extract_aworld_trajectory_record_payload(record: Mapping[str, Any], *, task_id: str) -> dict[str, Any]:
    trajectory = json.loads(record["trajectory"])
    return extract_aworld_trajectory_payload(
        trajectory,
        task_id=task_id,
        is_sub_task=record.get("is_sub_task"),
    )


def iter_aworld_trajectory_records(log_path: str | Path) -> Iterable[tuple[str, dict[str, Any]]]:
    path = Path(log_path).expanduser()
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = _parse_aworld_trajectory_log_line(line)
            except (SyntaxError, ValueError) as exc:
                raise ValueError(f"{path}:{line_number} is not a valid AWorld trajectory log record") from exc
            task_id = record.get("task_id")
            if task_id is None:
                raise ValueError(f"{path}:{line_number} missing required field: task_id")
            yield str(task_id), _extract_aworld_trajectory_record_payload(record, task_id=str(task_id))


def extract_aworld_trajectory_record(log_path: str | Path, task_id: str) -> dict[str, Any]:
    path = Path(log_path).expanduser()
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if task_id not in line:
                continue
            record = _parse_aworld_trajectory_log_line(line)
            if str(record.get("task_id")) == str(task_id):
                return _extract_aworld_trajectory_record_payload(record, task_id=str(task_id))
    raise ValueError(f"task_id {task_id} not found in {path}")


@dataclass(frozen=True)
class AWorldTrajectoryLogSource(_BaseEvalSource):
    path: str | Path
    task_ids: Iterable[str] | None
    extraction_dir: str | Path | None = None

    def iter_records(self) -> Iterable[EvalSourceRecord]:
        path = Path(self.path).expanduser()
        items = iter_aworld_trajectory_records(path) if self.task_ids is None else (
            (str(task_id), extract_aworld_trajectory_record(path, str(task_id)))
            for task_id in self.task_ids
        )
        for task_id, extracted in items:
            yield EvalSourceRecord(
                case_id=task_id,
                input={"task_id": task_id, "trajectory_log": str(path)},
                answer=extracted.get("final_answer"),
                metadata={
                    "source_kind": "trajectory",
                    "source_path": str(path),
                    "extraction_dir": str(Path(self.extraction_dir).expanduser()) if self.extraction_dir else None,
                },
                raw_payload=extracted,
            )

    def default_adapter(self):
        from aworld.evaluations.state_adapters import TrajectoryLogStateAdapter

        return TrajectoryLogStateAdapter(extraction_dir=self.extraction_dir)


def create_source_eval_suite(
    *,
    suite_id: str,
    source: EvalSource,
    judge_backend,
    judge_schema,
    gate_policy=None,
    state_adapter=None,
    runtime_harness=None,
    outcome_scorers=tuple(),
    reward_metrics=tuple(),
    standard_metrics=tuple(),
    trajectory_scorers=tuple(),
    metadata: Mapping[str, Any] | None = None,
):
    from aworld.evaluations.state_adapters import ReplayRuntimeHarness
    from aworld.evaluations.substrate import EvalSuiteDef

    records = list(source.iter_records())
    harness = runtime_harness
    if harness is None:
        adapter = state_adapter
        if adapter is None:
            adapter = source.default_adapter()
        harness = ReplayRuntimeHarness(adapter=adapter, records=tuple(records))
    return EvalSuiteDef(
        suite_id=suite_id,
        cases=[record.to_case() for record in records],
        runtime_harness=harness,
        judge_backend=judge_backend,
        judge_schema=judge_schema,
        gate_policy=gate_policy,
        outcome_scorers=tuple(outcome_scorers),
        reward_metrics=tuple(reward_metrics),
        standard_metrics=tuple(standard_metrics),
        trajectory_scorers=tuple(trajectory_scorers),
        metadata={
            **dict(metadata or {}),
            "source_backed": True,
        },
    )
