# coding: utf-8
from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from aworld.dataset.trajectory_io import (
    TrajectorySnapshot,
    read_trajectory_records,
)


_SCALAR_TYPES = (str, int, float, bool, type(None))
_MAX_EVIDENCE_CONTENT_CHARS = 30000


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


def _stringify_evidence_content(value: Any) -> tuple[str, bool, int]:
    if isinstance(value, str):
        content = value
    elif _is_serializable_value(value):
        content = json.dumps(value, ensure_ascii=False)
    else:
        content = str(value)
    original_length = len(content)
    if original_length <= _MAX_EVIDENCE_CONTENT_CHARS:
        return content, False, original_length
    head_size = _MAX_EVIDENCE_CONTENT_CHARS // 2
    tail_size = _MAX_EVIDENCE_CONTENT_CHARS - head_size
    omitted = original_length - _MAX_EVIDENCE_CONTENT_CHARS
    compacted = (
        f"{content[:head_size]}\n"
        f"... [truncated {omitted} chars from tool evidence] ...\n"
        f"{content[-tail_size:]}"
    )
    return compacted, True, original_length


def _evidence_record(
    *,
    source: str,
    content: Any,
    step: Any = None,
    msg_index: int | None = None,
    action_name: Any = None,
    tool_name: Any = None,
) -> dict[str, Any]:
    text, truncated, original_length = _stringify_evidence_content(content)
    record: dict[str, Any] = {
        "source": source,
        "content": text,
    }
    if step is not None:
        record["step"] = step
    if msg_index is not None:
        record["msg_index"] = msg_index
    if action_name:
        record["action_name"] = action_name
    if tool_name:
        record["tool_name"] = tool_name
    if truncated:
        record["truncated"] = True
        record["original_length"] = original_length
    return record


def _action_result_evidence(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    meta = item.get("meta", {}) if isinstance(item.get("meta"), Mapping) else {}
    state = item.get("state", {}) if isinstance(item.get("state"), Mapping) else {}
    state_input = state.get("input", {}) if isinstance(state.get("input"), Mapping) else {}
    action_results = state_input.get("action_result") or []
    if not isinstance(action_results, list):
        return []
    evidence = []
    for result in action_results:
        if not isinstance(result, Mapping):
            continue
        content = result.get("content")
        if content in (None, ""):
            continue
        evidence.append(
            _evidence_record(
                source="state.input.action_result",
                step=meta.get("step"),
                action_name=result.get("action_name"),
                tool_name=result.get("tool_name"),
                content=content,
            )
        )
    return evidence


def _tool_message_evidence(final_messages: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        _evidence_record(
            source="state.messages",
            msg_index=index,
            content=message.get("content"),
        )
        for index, message in enumerate(final_messages)
        if isinstance(message, Mapping) and message.get("role") == "tool" and message.get("content") not in (None, "")
    ]


def _dedupe_evidence(evidence: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for item in evidence:
        content = str(item.get("content") or "")
        key = (str(item.get("source") or ""), content)
        if not content or key in seen:
            continue
        seen.add(key)
        deduped.append(dict(item))
    return deduped


def _question_from_state_input(value: Any) -> str | None:
    """Extract a user question without promoting tool transport snapshots.

    AWorld providers have emitted ``state.input`` both as a mapping and as a
    serialized mapping string.  A non-empty ``action_result`` identifies the
    latter as a tool-result transport snapshot, not the original user prompt.
    Plain strings remain valid direct task inputs.
    """

    parsed = value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.startswith("{") and stripped.endswith("}"):
            for loader in (ast.literal_eval, json.loads):
                try:
                    candidate = loader(stripped)
                except (SyntaxError, ValueError, TypeError, json.JSONDecodeError):
                    continue
                if isinstance(candidate, Mapping):
                    parsed = candidate
                    break
        if isinstance(parsed, str):
            return parsed
    if not isinstance(parsed, Mapping):
        return None
    action_results = parsed.get("action_result")
    if isinstance(action_results, (list, tuple)) and action_results:
        return None
    content = parsed.get("content")
    return content if isinstance(content, str) and content.strip() else None


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
        question = (
            _question_from_state_input(first_state.get("input"))
            if isinstance(first_state, Mapping)
            else None
        )
        first_messages = first_state.get("messages", []) if isinstance(first_state, Mapping) else []
        if first_messages and isinstance(first_messages[0], Mapping) and first_messages[0].get("role") == "system":
            system_prompt = str(first_messages[0].get("content") or "")

    steps = []
    final_answer = None
    evidence_records: list[dict[str, Any]] = []
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
        evidence_records.extend(_action_result_evidence(item))

    final_messages = []
    if trajectory and isinstance(trajectory[-1], Mapping):
        final_state = trajectory[-1].get("state", {})
        if isinstance(final_state, Mapping):
            final_messages = final_state.get("messages", []) or []
    evidence_records.extend(_tool_message_evidence(final_messages))

    return {
        "task_id": task_id,
        "is_sub_task": is_sub_task,
        "num_steps": len(trajectory),
        "question": question,
        "system_prompt_excerpt": system_prompt[:8000],
        "steps": steps,
        "final_answer": final_answer,
        "evidence": _dedupe_evidence(evidence_records),
    }


def _extract_aworld_trajectory_record_payload(record: TrajectorySnapshot) -> dict[str, Any]:
    extracted = extract_aworld_trajectory_payload(
        record.trajectory or [],
        task_id=record.task_id,
        is_sub_task=record.is_sub_task,
    )
    extracted["trajectory_record"] = {
        "schema_version": record.schema_version,
        "revision": record.revision,
        "fidelity": record.fidelity,
        "build_result": dict(record.build_result),
        "trajectory_ref": record.trajectory_ref,
        "trajectory_checksum": record.trajectory_checksum,
        "record_checksum": record.record_checksum,
    }
    if record.evidence_bundle_path:
        extracted["evidence_bundle_path"] = record.evidence_bundle_path
    return extracted


def iter_aworld_trajectory_records(log_path: str | Path) -> Iterable[tuple[str, dict[str, Any]]]:
    path = Path(log_path).expanduser()
    result = read_trajectory_records(path)
    if not result.records:
        details = "; ".join(
            f"{item.source}:{item.line_number or '?'} {item.code}"
            for item in result.diagnostics[-3:]
        )
        suffix = f" ({details})" if details else ""
        raise ValueError(f"no valid AWorld trajectory records found in {path}{suffix}")
    for record in result.records:
        yield record.task_id, _extract_aworld_trajectory_record_payload(record)


def extract_aworld_trajectory_record(log_path: str | Path, task_id: str) -> dict[str, Any]:
    path = Path(log_path).expanduser()
    for record in read_trajectory_records(path).records:
        if record.task_id == str(task_id):
            return _extract_aworld_trajectory_record_payload(record)
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
