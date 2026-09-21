from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest

from aworld.core.trajectory import (
    TrajectoryBuildResult,
    TrajectoryBuildStatus,
    TrajectoryFidelity,
    TrajectorySourceKind,
    compute_trajectory_checksum,
)
from aworld.dataset.trajectory_io import (
    TrajectoryChecksumMismatchError,
    TrajectoryEnvelope,
    TrajectoryIOError,
    compute_record_checksum,
    read_trajectory_records,
)
from aworld.evaluations.sources import (
    AWorldTrajectoryLogSource,
    EvalSourceRecord,
    extract_aworld_trajectory_payload,
)
from aworld.evaluations.state_adapters import TrajectoryLogStateAdapter
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.evaluation import (
    EvaluationRequest,
    _aworld_trajectory_record,
    _aworld_trajectory_records_for_request,
)
from aworld.self_evolve.replay import (
    CandidateReplayMemberResult,
    CandidateReplayRequest,
    CandidateReplayResult,
    ReplayVariantResult,
    build_paired_replay_dataset,
    normalize_replay_members,
)
from aworld.self_evolve.replay_adaptation import (
    ReplayAdaptationBundle,
    ReplayCaseAdaptation,
)
from aworld.self_evolve.trace_pack import TrajectoryLogRecord, build_trace_pack
from aworld.self_evolve.trajectory_context import (
    build_trajectory_context_snapshots,
    input_with_reconstructed_context,
)
from aworld.self_evolve.types import CandidateVariant, DatasetRecipe, SelfEvolveTargetRef


CURRENT_TASK = "Continue the comparison using the two earlier synthetic sources."
PRIOR_ANSWER = "Earlier context: synthetic source A uses red; source B uses blue."
CURRENT_ANSWER_CANARY = "CURRENT_SOURCE_ANSWER_MUST_NOT_BECOME_CONTEXT"
EXPECTED_CANARY = "REFERENCE_ANSWER_MUST_NOT_BECOME_CONTEXT"
REPLAY_ANSWER = "The synthetic comparison is complete."
TOOL_EVIDENCE = "Observed synthetic values: red and blue."


def _dataset(*cases: EvalCase) -> SelfEvolveDataset:
    return SelfEvolveDataset(
        cases=cases,
        recipe=DatasetRecipe(
            source={"kind": "test"},
            split_seed="task-context-test",
            splits={"train": [case.case_id for case in cases], "validation": [], "held_out": []},
        ),
    )


def _candidate() -> CandidateVariant:
    return CandidateVariant(
        candidate_id="candidate-context",
        target=SelfEvolveTargetRef(target_type="skill", target_id="synthetic-context"),
        content="# Synthetic candidate\n",
        rationale="Synthetic task-context regression fixture",
    )


def _fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _trajectory(*, finished: bool = True) -> list[dict[str, Any]]:
    return [
        {
            "meta": {"step": 1, "agent_id": "synthetic-agent"},
            "state": {
                "input": {"content": CURRENT_TASK},
                "messages": [{"role": "system", "content": "Compare synthetic sources."}],
            },
            "action": {
                "content": "Read the fixture.",
                "tool_calls": [{"function": {"name": "fixture_reader", "arguments": "{}"}}],
                "is_agent_finished": False,
            },
        },
        {
            "meta": {"step": 2, "agent_id": "synthetic-agent"},
            "state": {
                "input": {
                    "content": "Tool transport summary; this is not a user request.",
                    "action_result": [{"tool_name": "fixture_reader", "content": TOOL_EVIDENCE}],
                },
                "messages": [{"role": "tool", "content": TOOL_EVIDENCE}],
            },
            "action": {"content": REPLAY_ANSWER, "tool_calls": [], "is_agent_finished": finished},
        },
    ]


def _source_case_with_history() -> EvalCase:
    records = tuple(
        TrajectoryLogRecord(
            record_index=index,
            task_id=task_id,
            record_metadata={"task_id": task_id, "session_id": "synthetic-session"},
            trajectory=(
                {
                    "state": {"input": {"content": task}},
                    "action": {"content": answer, "is_agent_finished": True},
                },
            ),
        )
        for index, (task_id, task, answer) in enumerate(
            (
                ("prior-task", "Compare synthetic source A and source B.", PRIOR_ANSWER),
                ("current-task", CURRENT_TASK, CURRENT_ANSWER_CANARY),
            )
        )
    )
    snapshot = build_trajectory_context_snapshots(records)[1]
    return EvalCase(
        case_id="current-task",
        input={"content": CURRENT_TASK},
        expected_output=EXPECTED_CANARY,
        context_snapshot=snapshot,
    )


def _paired_dataset(
    tmp_path: Path,
    *,
    case: EvalCase,
    executed_input: Any,
    baseline_trajectory: list[dict[str, Any]] | None = None,
    candidate_trajectory: list[dict[str, Any]] | None = None,
) -> SelfEvolveDataset:
    candidate = _candidate()
    dataset = _dataset(case)
    adaptation = ReplayAdaptationBundle(
        schema_version="synthetic-test",
        source_workspace_root=str(tmp_path),
        workspace_seed=str(tmp_path / "seed"),
        workspace_seed_fingerprint=_fingerprint("seed"),
        manifest_path=str(tmp_path / "manifest.json"),
        environment_snapshot_path=str(tmp_path / "environment.json"),
        environment_fingerprint=_fingerprint("environment"),
        cases=(
            ReplayCaseAdaptation(
                case_id=case.case_id,
                adapted_task_input=executed_input,
                task_input_fingerprint=_fingerprint(executed_input),
                dependencies=(),
                bindings=(),
                tool_names=(),
                readiness="ready",
            ),
        ),
        adaptation_fingerprint=_fingerprint("adaptation"),
        ready=True,
    )
    root_request = CandidateReplayRequest(
        run_id="synthetic-context-run",
        task_id="aggregate-root",
        workspace_root=str(tmp_path),
        target=candidate.target,
        candidate_id=candidate.candidate_id,
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input="Unrelated root request input must not reach the judge.",
        replay_adaptation=adaptation,
    )
    member_request = replace(
        root_request,
        task_id=case.case_id,
        task_input=executed_input,
        task_input_fingerprint=_fingerprint(executed_input),
    )
    baseline = ReplayVariantResult(
        variant_id="baseline",
        status="succeeded",
        trajectory=baseline_trajectory if baseline_trajectory is not None else _trajectory()[-1:],
    )
    treatment = ReplayVariantResult(
        variant_id=candidate.candidate_id,
        status="succeeded",
        trajectory=candidate_trajectory if candidate_trajectory is not None else _trajectory()[-1:],
    )
    replay = CandidateReplayResult(
        request=root_request,
        baseline=baseline,
        candidate=treatment,
        member_results=(CandidateReplayMemberResult(case.case_id, member_request, baseline, treatment),),
    )
    normalized = normalize_replay_members(dataset=dataset, replay_result=replay)
    assert normalized.valid, normalized.failure_events
    assert normalized.members[0].request.task_input == executed_input
    return build_paired_replay_dataset(dataset=dataset, replay_result=replay, candidate=candidate)


def _pipeline(tmp_path: Path, record: Mapping[str, Any], *, label: str = "trajectory"):
    path = tmp_path / f"{label}.log"
    path.write_text(repr(dict(record)) + "\n", encoding="utf-8")
    read_result = read_trajectory_records(path)
    assert read_result.diagnostics == ()
    assert len(read_result.records) == 1
    source = AWorldTrajectoryLogSource(path=path, task_ids=None)
    source_records = list(source.iter_records())
    assert len(source_records) == 1
    source_record = source_records[0]
    state = source.default_adapter().adapt(record=source_record, case=source_record.to_case(), target={})
    return read_result.records[0], source_record.raw_payload, state


def test_full_and_terminal_compacted_replays_receive_same_question_and_prior_context(tmp_path: Path) -> None:
    case = _source_case_with_history()
    executed_input = input_with_reconstructed_context(case.input, case.context_snapshot)
    executed_input["content"] += "\nUse the adapted synthetic fixture paths."
    paired = _paired_dataset(
        tmp_path,
        case=case,
        executed_input=executed_input,
        baseline_trajectory=_trajectory(),
        candidate_trajectory=_trajectory()[-1:],
    )
    assert paired.cases[0].input == case.input
    assert paired.cases[0].metadata["replay"]["request"]["task_context"] == executed_input["content"]
    results = []
    for candidate in (None, _candidate()):
        variant_id = candidate.candidate_id if candidate is not None else "baseline"
        request = EvaluationRequest(variant_id=variant_id, candidate=candidate, dataset=paired)
        record = _aworld_trajectory_records_for_request(request)[0]
        snapshot, extracted, state = _pipeline(tmp_path, record, label=variant_id)
        assert snapshot.task_context == executed_input["content"]
        assert extracted["question"] == executed_input["content"]
        assert CURRENT_TASK in extracted["question"]
        assert PRIOR_ANSWER in extracted["question"]
        assert state.status == "success"
        assert state.outcome["is_finished"] is True
        assert state.outcome["raw_evidence_blocks"] == 2
        assert extracted["final_answer"] == REPLAY_ANSWER
        for canary in (EXPECTED_CANARY, CURRENT_ANSWER_CANARY, "Unrelated root request input"):
            assert canary not in json.dumps(record)
            assert canary not in json.dumps(extracted)
        results.append(extracted)
    assert results[0]["question"] == results[1]["question"]
    assert results[0]["num_steps"] == 2
    assert results[1]["num_steps"] == 1


@pytest.mark.parametrize("field", ("content", "task", "prompt", "input"))
def test_paired_context_projects_only_supported_textual_input_fields(tmp_path: Path, field: str) -> None:
    case = _source_case_with_history()
    executed_input = {
        field: "The adapted synthetic request.",
        "expected_output": EXPECTED_CANARY,
        "answer": CURRENT_ANSWER_CANARY,
        "metadata": {"answer": CURRENT_ANSWER_CANARY},
    }
    paired = _paired_dataset(tmp_path, case=case, executed_input=executed_input)
    request = EvaluationRequest(variant_id="baseline", candidate=None, dataset=paired)
    record = _aworld_trajectory_records_for_request(request)[0]
    _, extracted, _ = _pipeline(tmp_path, record)
    assert record["task_context"] == executed_input[field]
    assert extracted["question"] == executed_input[field]
    assert EXPECTED_CANARY not in json.dumps(record)
    assert CURRENT_ANSWER_CANARY not in json.dumps(record)


@pytest.mark.parametrize(
    "executed_input",
    (
        None,
        "   ",
        {},
        17,
        ["A list is not task text."],
        {"expected_output": EXPECTED_CANARY, "answer": CURRENT_ANSWER_CANARY},
        {"content": {"answer": CURRENT_ANSWER_CANARY}},
        {"content": "", "task": "UNSEEN request must not reach the judge."},
        {"content": " \n ", "task": "UNSEEN request must not reach the judge."},
        {"content": "Transport content", "action_result": [{"content": TOOL_EVIDENCE}]},
        json.dumps({"content": "Transport content", "action_result": [{"content": TOOL_EVIDENCE}]}),
        repr({"content": "Transport content", "action_result": [{"content": TOOL_EVIDENCE}]}),
    ),
)
def test_missing_or_tool_result_input_cannot_supply_paired_context(tmp_path: Path, executed_input: Any) -> None:
    paired = _paired_dataset(tmp_path, case=_source_case_with_history(), executed_input=executed_input)
    request = EvaluationRequest(variant_id="baseline", candidate=None, dataset=paired)
    record = _aworld_trajectory_records_for_request(request)[0]
    snapshot, extracted, state = _pipeline(tmp_path, record)
    assert paired.cases[0].metadata["replay"]["request"].get("task_context") is None
    assert record.get("task_context") is None
    assert snapshot.task_context is None
    assert extracted["question"] is None
    assert state.outcome["raw_evidence_blocks"] == 2
    assert state.status == "success"
    assert EXPECTED_CANARY not in json.dumps(record)
    assert CURRENT_ANSWER_CANARY not in json.dumps(record)


@pytest.mark.parametrize("field", (None, "content", "task", "prompt", "input"))
def test_nonpaired_record_uses_case_input_without_current_answer_leakage(tmp_path: Path, field: str | None) -> None:
    context = "The executed request includes synthetic prior context."
    case_input = context if field is None else {field: context, "answer": EXPECTED_CANARY}
    case = replace(
        _source_case_with_history(),
        input=case_input,
        trace_pack=build_trace_pack(_trajectory()[-1:], source_kind="current_trajectory", task_id="current-task"),
    )
    request = EvaluationRequest(variant_id="baseline", candidate=None, dataset=_dataset(case))
    record = _aworld_trajectory_record(case, request=request)
    snapshot, extracted, _ = _pipeline(tmp_path, record)
    assert snapshot.task_context == context
    assert extracted["question"] == context
    assert EXPECTED_CANARY not in json.dumps(record)
    assert CURRENT_ANSWER_CANARY not in json.dumps(record)


def test_different_contexts_do_not_deduplicate_identical_replay_outputs(tmp_path: Path) -> None:
    cases = []
    for case_id, context in (("red", "Compare red."), ("blue", "Compare blue."), ("red-again", "Compare red.")):
        paired = _paired_dataset(tmp_path, case=EvalCase(case_id=case_id, input="Original task."), executed_input=context)
        cases.append(paired.cases[0])
    request = EvaluationRequest(variant_id="baseline", candidate=None, dataset=_dataset(*cases))
    records = _aworld_trajectory_records_for_request(request)
    assert [record["task_id"] for record in records] == ["red", "blue"]
    assert [record["task_context"] for record in records] == ["Compare red.", "Compare blue."]
    assert records[0]["trajectory"] == records[1]["trajectory"]


@pytest.mark.parametrize("finished", (False, True))
@pytest.mark.parametrize("compacted", (False, True))
def test_explicit_context_changes_only_question_not_evidence_actions_or_completion(finished: bool, compacted: bool) -> None:
    trajectory = _trajectory(finished=finished)
    if compacted:
        trajectory = trajectory[-1:]
    original = copy.deepcopy(trajectory)
    without_context = extract_aworld_trajectory_payload(trajectory, task_id="task-context")
    with_context = extract_aworld_trajectory_payload(
        trajectory, task_id="task-context", task_context="Explicit request and synthetic prior context."
    )
    assert with_context["question"] == "Explicit request and synthetic prior context."
    assert {key: value for key, value in with_context.items() if key != "question"} == {
        key: value for key, value in without_context.items() if key != "question"
    }
    assert trajectory == original
    states = []
    for payload in (without_context, with_context):
        record = EvalSourceRecord(case_id="task-context", input={}, raw_payload=payload)
        states.append(TrajectoryLogStateAdapter().adapt(record=record, case=record.to_case(), target={}))
    assert states[0].status == states[1].status == ("success" if finished else "failed")
    assert states[0].tool_calls == states[1].tool_calls
    assert states[0].standard_metrics == states[1].standard_metrics
    assert states[0].outcome["raw_evidence_blocks"] == states[1].outcome["raw_evidence_blocks"] == 2
    assert states[0].outcome["is_finished"] is states[1].outcome["is_finished"] is finished


@pytest.mark.parametrize("compacted", (False, True))
def test_legacy_logs_without_task_context_keep_first_state_fallback(tmp_path: Path, compacted: bool) -> None:
    trajectory = _trajectory()[-1:] if compacted else _trajectory()
    record = {"task_id": "legacy-task", "is_sub_task": False, "trajectory": json.dumps(trajectory)}
    snapshot, extracted, state = _pipeline(tmp_path, record)
    assert snapshot.task_context is None
    assert extracted["question"] == (None if compacted else CURRENT_TASK)
    assert state.status == "success"
    assert state.outcome["raw_evidence_blocks"] == 2


def _envelope(context: str | None) -> TrajectoryEnvelope:
    trajectory = _trajectory()[-1:]
    build = TrajectoryBuildResult(
        task_id="v2-context-task",
        session_id="synthetic-session",
        trace_id="synthetic-trace",
        task_epoch=0,
        status=TrajectoryBuildStatus.COMPLETE,
        fidelity=TrajectoryFidelity.COMPLETE,
        reason_code=None,
        source_kind=TrajectorySourceKind.EVENT_STATE,
        source_high_watermark=1,
        scheduled_updates=1,
        completed_updates=1,
        failed_updates=0,
        pending_updates=0,
        source_agent_messages=1,
        llm_call_count=1,
        tool_call_count=0,
        persisted_items=1,
        trajectory_ref=None,
        source_checksum=None,
        trajectory_checksum=compute_trajectory_checksum(trajectory),
        builder_version="synthetic-context-test",
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    return TrajectoryEnvelope(build_result=build, revision=1, trajectory=trajectory, task_context=context)


@pytest.mark.parametrize("context", (None, "Current synthetic request.\nEarlier context: red and blue."))
def test_v2_task_context_roundtrips_through_codec_and_source(tmp_path: Path, context: str | None) -> None:
    envelope = _envelope(context)
    payload = envelope.to_dict()
    if context is None:
        assert "task_context" not in payload
    restored = TrajectoryEnvelope.from_dict(payload)
    assert restored.task_context == context
    assert restored.to_dict() == payload
    path = tmp_path / "trajectory.jsonl"
    path.write_bytes(restored.to_json_line())
    result = read_trajectory_records(path)
    assert result.diagnostics == ()
    assert result.records[0].task_context == context
    assert result.records[0].trajectory == envelope.trajectory
    assert result.records[0].record_checksum == payload["integrity"]["record_checksum"]
    source_record = next(iter(AWorldTrajectoryLogSource(path=path, task_ids=None).iter_records()))
    assert source_record.raw_payload["question"] == context
    assert source_record.raw_payload["final_answer"] == REPLAY_ANSWER
    assert len(source_record.raw_payload["evidence"]) == 2


def test_v2_rejects_explicit_null_context_even_with_valid_record_checksum(tmp_path: Path) -> None:
    payload = _envelope(None).to_dict()
    payload["task_context"] = None
    payload["integrity"].pop("record_checksum")
    payload["integrity"]["record_checksum"] = compute_record_checksum(payload)

    with pytest.raises(TrajectoryIOError, match="v2 task_context must be omitted or text"):
        TrajectoryEnvelope.from_dict(payload)

    path = tmp_path / "null-context.jsonl"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    result = read_trajectory_records(path)
    assert result.records == ()
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].code == "malformed_record"
    assert "v2 task_context must be omitted or text" in result.diagnostics[0].message


def test_v2_record_checksum_protects_task_context_without_changing_trajectory_checksum() -> None:
    envelope = _envelope("Original synthetic request.")
    changed = replace(envelope, task_context="Different synthetic request.")
    original_payload = envelope.to_dict()
    changed_payload = changed.to_dict()
    assert original_payload["integrity"]["trajectory_checksum"] == changed_payload["integrity"]["trajectory_checksum"]
    assert original_payload["integrity"]["record_checksum"] != changed_payload["integrity"]["record_checksum"]
    tampered = copy.deepcopy(original_payload)
    tampered["task_context"] = changed.task_context
    with pytest.raises(TrajectoryChecksumMismatchError, match="record checksum mismatch"):
        TrajectoryEnvelope.from_dict(tampered)
