from __future__ import annotations

import json
from pathlib import Path

from aworld.self_evolve.trace_pack import build_trace_pack, trace_packs_from_trajectory_log


def _trajectory_item(step: int, content: str, *, tool_name: str | None = None) -> dict:
    tool_calls = []
    if tool_name is not None:
        tool_calls.append({"function": {"name": tool_name, "arguments": "{\"path\": \"demo\"}"}})
    return {
        "meta": {"step": step, "agent_id": "agent", "pre_agent": "runner"},
        "state": {
            "input": {"content": "Fix the generated report."},
            "messages": [{"role": "user", "content": "Fix the generated report."}],
        },
        "action": {
            "content": content,
            "tool_calls": tool_calls,
            "is_agent_finished": step == 4,
        },
        "reward": {
            "status": "failed" if step == 3 else "ok",
            "score": 0.2 if step == 3 else None,
            "tool_outputs": [{"content": "timeout from generated artifact"}] if step == 3 else [],
        },
    }


def test_trace_pack_preserves_sar_fields_and_stable_evidence_ids() -> None:
    trajectory = [
        _trajectory_item(1, "I will inspect the report.", tool_name="filesystem.read"),
        _trajectory_item(2, "The artifact is missing required anchors."),
        _trajectory_item(3, "Verification failed for generated report.", tool_name="pytest"),
        _trajectory_item(4, "I cannot verify the requested artifact."),
    ]

    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="task-1",
        max_steps=10,
    )

    assert trace_pack.task_id == "task-1"
    assert trace_pack.source_kind == "current_trajectory"
    assert [step.evidence_id for step in trace_pack.steps] == [
        "task-1:step-1",
        "task-1:step-2",
        "task-1:step-3",
        "task-1:step-4",
    ]
    assert trace_pack.steps[0].state["input"]["content"] == "Fix the generated report."
    assert trace_pack.steps[0].action["tool_calls"][0]["function"]["name"] == "filesystem.read"
    assert trace_pack.steps[2].reward["status"] == "failed"
    assert trace_pack.steps[2].tool_names == ("pytest",)
    assert trace_pack.final_action_excerpt == "I cannot verify the requested artifact."


def test_trace_pack_compresses_middle_steps_and_enforces_text_budget() -> None:
    trajectory = [
        _trajectory_item(1, "start " + "a" * 100),
        _trajectory_item(2, "middle-1 " + "b" * 100),
        _trajectory_item(3, "middle-2 " + "c" * 100),
        _trajectory_item(4, "end " + "d" * 100),
    ]

    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="task-2",
        max_steps=2,
        max_text_chars=40,
    )

    assert [step.evidence_id for step in trace_pack.steps] == [
        "task-2:step-1",
        "task-2:step-4",
    ]
    assert trace_pack.omitted_step_count == 2
    assert trace_pack.compression_summary == "omitted 2 middle step(s): task-2:step-2, task-2:step-3"
    assert trace_pack.steps[0].action["content"].endswith("...")
    assert len(trace_pack.steps[0].action["content"]) <= 40


def test_trace_pack_samples_long_trajectory_across_middle_interactions() -> None:
    trajectory = [
        _trajectory_item(index + 1, f"step-{index + 1}", tool_name=f"tool-{index + 1}")
        for index in range(61)
    ]

    trace_pack = build_trace_pack(
        trajectory,
        source_kind="trajectory_log",
        task_id="long-task",
        max_steps=8,
    )

    source_indexes = [step.source_index for step in trace_pack.steps]
    assert source_indexes[:2] == [0, 1]
    assert source_indexes[-2:] == [59, 60]
    assert len(source_indexes) == 8
    assert any(10 <= index < 25 for index in source_indexes)
    assert any(25 <= index < 40 for index in source_indexes)
    assert any(40 <= index < 59 for index in source_indexes)


def test_trace_packs_from_trajectory_log_requires_explicit_log_path() -> None:
    fixture = Path(__file__).parent / "fixtures" / "credit_assignment_cases" / "sample_trajectory.log"

    trace_packs = trace_packs_from_trajectory_log(fixture, max_steps=3)

    assert trace_packs
    first_pack = trace_packs[0]
    assert first_pack.source_kind == "trajectory_log"
    assert first_pack.task_id == "fa27d89c63e911f18b676a5e5d18257e"
    assert first_pack.steps[0].state["input"]["content"] == (
        "Generate yesterday health report from ~/Documents/health."
    )


def test_trace_packs_from_trajectory_log_accepts_prefixed_log_lines(tmp_path: Path) -> None:
    trajectory = [_trajectory_item(1, "Recovered from prefixed log.")]
    record = {
        "task_id": "task-prefixed",
        "is_sub_task": False,
        "trajectory": json.dumps(trajectory),
    }
    log_path = tmp_path / "trajectory.log"
    log_path.write_text(
        "\x1b[32m| 2026-06-09 19:35:42.360 | INFO | trajectory | "
        + repr(record)
        + "\x1b[0m\n",
        encoding="utf-8",
    )

    trace_packs = trace_packs_from_trajectory_log(log_path)

    assert len(trace_packs) == 1
    assert trace_packs[0].task_id == "task-prefixed"
    assert trace_packs[0].steps[0].action["content"] == "Recovered from prefixed log."


def test_trajectory_log_contract_preserves_sar_steps_with_extended_record_fields(
    tmp_path: Path,
) -> None:
    old_style_trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent"},
            "state": {"input": {"content": "old style task"}},
            "action": {"content": "old style action", "tool_calls": []},
            "reward": {"status": "ok"},
        }
    ]
    extended_trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {
                "input": {"content": "extended style task"},
                "messages": [{"role": "user", "content": "extended style task"}],
            },
            "action": {
                "content": "extended style action",
                "tool_calls": [{"function": {"name": "artifact.read"}}],
                "is_agent_finished": True,
            },
            "reward": {"status": "ok", "score": 1.0},
        }
    ]
    log_path = tmp_path / "trajectory.log"
    log_path.write_text(
        "\n".join(
            [
                repr(
                    {
                        "task_id": "old-task",
                        "trajectory": json.dumps(old_style_trajectory, ensure_ascii=False),
                    }
                ),
                repr(
                    {
                        "task_id": "extended-task",
                        "is_sub_task": False,
                        "trajectory": json.dumps(extended_trajectory, ensure_ascii=False),
                        "token_id_trajectory": None,
                        "llm_calls": json.dumps([{"model": "test-model"}]),
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    old_pack, extended_pack = trace_packs_from_trajectory_log(log_path, max_steps=8)

    assert old_pack.task_id == "old-task"
    assert old_pack.steps[0].state["input"]["content"] == "old style task"
    assert old_pack.steps[0].action["content"] == "old style action"
    assert old_pack.steps[0].reward["status"] == "ok"
    assert extended_pack.task_id == "extended-task"
    assert extended_pack.steps[0].state["input"]["content"] == "extended style task"
    assert extended_pack.steps[0].action["tool_calls"][0]["function"]["name"] == "artifact.read"
    assert extended_pack.steps[0].reward["score"] == 1.0
