from __future__ import annotations

import json
from pathlib import Path

import pytest

from aworld.self_evolve.datasets import (
    EvalCase,
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
    build_dataset_recipe,
    load_jsonl_eval_cases,
)


def test_eval_source_config_defaults_to_current_trajectory_and_validates_kind() -> None:
    config = SelfEvolveEvalSourceConfig()

    assert config.kind == "current_trajectory"
    assert config.task_ids == ()
    assert config.max_cases == 100

    for kind in ["trajectory_log", "session", "jsonl", "batch_config"]:
        assert SelfEvolveEvalSourceConfig(kind=kind).kind == kind

    with pytest.raises(ValueError, match="unsupported eval source kind"):
        SelfEvolveEvalSourceConfig(kind="developer-local-log")


def test_jsonl_loader_preserves_explicit_cases_and_defaults_missing_case_ids(tmp_path) -> None:
    path = tmp_path / "eval_cases.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "case_id": "case-explicit",
                        "input": {"task": "fix browser login"},
                        "expected_output": "login works",
                        "metadata": {"target": "skill"},
                    }
                ),
                json.dumps(
                    {
                        "id": "case-id-field",
                        "input": "summarize page",
                        "verification_command": "pytest tests/demo.py",
                    }
                ),
                json.dumps({"input": "unnamed case"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    cases = load_jsonl_eval_cases(path, max_cases=2)

    assert [case.case_id for case in cases] == ["case-explicit", "case-id-field"]
    assert cases[0].input == {"task": "fix browser login"}
    assert cases[0].expected_output == "login works"
    assert cases[0].metadata["target"] == "skill"
    assert cases[1].verification_command == "pytest tests/demo.py"
    assert cases[1].source["kind"] == "jsonl"
    assert cases[1].source["line_number"] == 2

    all_cases = load_jsonl_eval_cases(path)
    assert all_cases[2].case_id == "eval_cases:line-3"


def test_dataset_recipe_records_identity_and_deterministic_splits(tmp_path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "".join(
            json.dumps({"case_id": f"case-{index}", "input": f"task {index}"}) + "\n"
            for index in range(5)
        ),
        encoding="utf-8",
    )
    config = SelfEvolveEvalSourceConfig(kind="jsonl", path=str(path), max_cases=5)
    cases = load_jsonl_eval_cases(path)

    first_recipe = build_dataset_recipe(cases, source_config=config, split_seed="seed-a")
    second_recipe = build_dataset_recipe(cases, source_config=config, split_seed="seed-a")

    assert first_recipe == second_recipe
    assert first_recipe.source["kind"] == "jsonl"
    assert first_recipe.source["path"] == str(path)
    assert first_recipe.source["fingerprint"].startswith("sha256:")
    assert first_recipe.source["case_count"] == 5
    assert len(first_recipe.splits["train"]) == 3
    assert len(first_recipe.splits["validation"]) == 1
    assert len(first_recipe.splits["held_out"]) == 1
    assert set(first_recipe.trainable_case_ids) == set(
        first_recipe.splits["train"] + first_recipe.splits["validation"]
    )
    assert tuple(first_recipe.splits["held_out"]) == first_recipe.held_out_case_ids


def test_build_dataset_from_jsonl_applies_task_id_filter_and_records_it(tmp_path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"case_id": "task-1", "input": "one"}),
                json.dumps({"case_id": "task-2", "input": "two"}),
                json.dumps({"case_id": "task-3", "input": "three"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(
            kind="jsonl",
            path=str(path),
            task_ids=("task-2", "task-3"),
        ),
        split_seed="seed-filtered",
    )

    assert [case.case_id for case in dataset.cases] == ["task-2", "task-3"]
    assert dataset.recipe.source["task_ids"] == ["task-2", "task-3"]
    assert dataset.recipe.source["case_count"] == 2


def test_build_dataset_from_current_trajectory_and_trajectory_log_sources(tmp_path) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix generated report."}},
            "action": {"content": "I will inspect it.", "tool_calls": []},
            "reward": {"status": "ok"},
        }
    ]
    current_dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="current-task",
        split_seed="seed-current",
    )

    assert current_dataset.cases == (
        EvalCase(
            case_id="current-task",
            input={"content": "Fix generated report."},
            trace_pack=current_dataset.cases[0].trace_pack,
            source={"kind": "current_trajectory", "task_id": "current-task"},
        ),
    )
    assert current_dataset.cases[0].trace_pack.task_id == "current-task"
    assert current_dataset.recipe.source["kind"] == "current_trajectory"

    fixture_log = (
        Path(__file__).parent
        / "fixtures"
        / "credit_assignment_cases"
        / "sample_trajectory.log"
    )
    log_dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="trajectory_log", path=str(fixture_log), max_cases=2),
        split_seed="seed-log",
    )

    assert len(log_dataset.cases) == 2
    assert log_dataset.cases[0].trace_pack.source_kind == "trajectory_log"
    assert log_dataset.recipe.source["path"] == str(fixture_log)
    assert log_dataset.recipe.source["case_count"] == 2


def test_build_dataset_from_trajectory_set_v1_contract(tmp_path) -> None:
    trajectory_path = tmp_path / "trajectories" / "baseline.log"
    trajectory_path.parent.mkdir()
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Summarize a page with evidence."}},
            "action": {"content": "I will gather bounded evidence."},
            "reward": {"status": "ok"},
        }
    ]
    trajectory_path.write_text(
        repr(
            {
                "task_id": "task-a",
                "is_sub_task": False,
                "trajectory": json.dumps(trajectory),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    set_path = tmp_path / "trajectory_set.json"
    set_path.write_text(
        json.dumps(
            {
                "schema_version": "aworld.self_evolve.trajectory_set.v1",
                "set_id": "set-a",
                "target": {"target_type": "skill", "target_id": "demo"},
                "members": [
                    {
                        "member_id": "baseline-a",
                        "role": "baseline",
                        "trajectory_path": "trajectories/baseline.log",
                        "task_id": "task-a",
                        "task_input_digest": "sha256:abc",
                        "evidence_bundle_path": "evidence/bundle.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="trajectory_set", path=str(set_path)),
        split_seed="seed-set",
    )

    assert [case.case_id for case in dataset.cases] == ["task-a"]
    assert dataset.cases[0].trace_pack is not None
    assert dataset.cases[0].trace_pack.task_id == "task-a"
    assert dataset.cases[0].source["kind"] == "trajectory_set"
    assert dataset.cases[0].source["set_id"] == "set-a"
    assert dataset.cases[0].source["member_id"] == "baseline-a"
    assert dataset.cases[0].source["role"] == "baseline"
    assert dataset.cases[0].metadata["trajectory_set"]["target"]["target_id"] == "demo"
    assert dataset.recipe.source["kind"] == "trajectory_set"
    assert dataset.recipe.source["path"] == str(set_path)


def test_build_dataset_from_trajectory_set_rejects_untrusted_absolute_path(tmp_path) -> None:
    outside = tmp_path.parent / "outside.log"
    outside.write_text("", encoding="utf-8")
    set_path = tmp_path / "trajectory_set.json"
    set_path.write_text(
        json.dumps(
            {
                "schema_version": "aworld.self_evolve.trajectory_set.v1",
                "set_id": "set-a",
                "target": {"target_type": "skill", "target_id": "demo"},
                "members": [
                    {
                        "member_id": "bad",
                        "role": "baseline",
                        "trajectory_path": str(outside),
                        "task_id": "task-a",
                        "task_input_digest": "sha256:abc",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="members\\[0\\]\\.trajectory_path"):
        build_dataset_from_source(
            SelfEvolveEvalSourceConfig(kind="trajectory_set", path=str(set_path)),
            split_seed="seed-set",
        )


def test_build_dataset_from_user_documents_trajectory_log_seed(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    log_path = home / "Documents" / "logs" / "trajectory.log"
    log_path.parent.mkdir(parents=True)
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Seed regression benchmark."}},
            "action": {"content": "I will preserve benchmark evidence."},
            "reward": {"status": "ok"},
        }
    ]
    log_path.write_text(
        repr(
            {
                "task_id": "seed-task",
                "is_sub_task": False,
                "trajectory": json.dumps(trajectory),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(
            kind="trajectory_log",
            path="~/Documents/logs/trajectory.log",
        ),
        split_seed="seed-benchmark",
    )

    assert [case.case_id for case in dataset.cases] == ["seed-task"]
    assert dataset.recipe.source["kind"] == "trajectory_log"
    assert dataset.recipe.source["path"] == str(log_path)
    assert dataset.recipe.source["fingerprint"].startswith("sha256:")


def test_build_dataset_from_session_source_reads_explicit_workspace_session_log(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    session_log = workspace / ".aworld" / "memory" / "sessions" / "session-1.jsonl"
    session_log.parent.mkdir(parents=True)
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Use pnpm."}},
            "action": {"content": "I will inspect package manager usage."},
            "reward": {"status": "ok"},
        }
    ]
    session_log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "session_id": "session-1",
                        "task_id": "task-1",
                        "input": {"content": "Use pnpm."},
                        "final_answer": "Use pnpm and keep tests fast.",
                        "task_status": "completed",
                        "trajectory": trajectory,
                    }
                ),
                json.dumps(
                    {
                        "session_id": "session-1",
                        "task_id": "task-2",
                        "input": {"content": "Document release."},
                        "final_answer": "Document release steps.",
                        "task_status": "completed",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(
            kind="session",
            path=str(workspace),
            session_id="session-1",
            max_cases=1,
        ),
        split_seed="seed-session",
    )

    assert [case.case_id for case in dataset.cases] == ["task-1"]
    assert dataset.cases[0].input == {"content": "Use pnpm."}
    assert dataset.cases[0].expected_output == "Use pnpm and keep tests fast."
    assert dataset.cases[0].trace_pack is not None
    assert dataset.cases[0].trace_pack.source_kind == "session"
    assert dataset.cases[0].trace_pack.task_id == "task-1"
    assert dataset.cases[0].metadata["task_status"] == "completed"
    assert dataset.recipe.source["kind"] == "session"
    assert dataset.recipe.source["session_id"] == "session-1"
    assert dataset.recipe.source["path"] == str(workspace)


def test_build_dataset_from_batch_config_reuses_evaluation_dataset_columns(tmp_path) -> None:
    dataset_path = tmp_path / "agent_eval.jsonl"
    dataset_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "case_id": "math-1",
                        "question": "1+1?",
                        "answer": "2",
                        "difficulty": "easy",
                    }
                ),
                json.dumps(
                    {
                        "case_id": "math-2",
                        "question": "2+2?",
                        "answer": "4",
                        "difficulty": "easy",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "batch_config.json"
    config_path.write_text(
        json.dumps(
            {
                "eval_dataset_id_or_file_path": "agent_eval.jsonl",
                "eval_dataset_query_column": "question",
                "eval_dataset_answer_column": "answer",
            }
        ),
        encoding="utf-8",
    )

    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="batch_config", path=str(config_path)),
        split_seed="seed-batch",
    )

    assert [case.case_id for case in dataset.cases] == ["math-1", "math-2"]
    assert dataset.cases[0].input == "1+1?"
    assert dataset.cases[0].expected_output == "2"
    assert dataset.cases[0].metadata == {"difficulty": "easy"}
    assert dataset.cases[0].source["dataset_path"] == str(dataset_path)
    assert dataset.recipe.source["kind"] == "batch_config"
    assert dataset.recipe.source["path"] == str(config_path)
