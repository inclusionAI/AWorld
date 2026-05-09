import json
from pathlib import Path

from aworld_cli.memory.cache_observability import summarize_cache_observability


def _write_session_log(path: Path, payloads: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")


def test_summarize_cache_observability_aggregates_request_linked_usage(tmp_path):
    workspace = tmp_path / "workspace"
    session_log = workspace / ".aworld" / "memory" / "sessions" / "session-1.jsonl"
    repeated_prefix = [
        {"role": "system", "content": "You are Aworld. Follow workspace guidance carefully."},
        {"role": "user", "content": "Please inspect the repo and explain the failing tests."},
    ]
    _write_session_log(
        session_log,
        [
            {
                "recorded_at": "2026-05-07T00:00:00+00:00",
                "event": "task_completed",
                "task_id": "task-1",
                "llm_calls": [
                    {
                        "task_id": "task-1",
                        "request_id": "llm_req_1",
                        "provider_request_id": "req_provider_1",
                        "model": "gpt-4.1",
                        "request": {"messages": repeated_prefix + [{"role": "assistant", "content": "Working..."}]},
                        "usage_raw": {
                            "cache_hit_tokens": 80,
                            "cache_write_tokens": 20,
                            "prompt_tokens_details": {"cached_tokens": 80},
                        },
                    },
                    {
                        "task_id": "task-1",
                        "request_id": "llm_req_2",
                        "provider_request_id": "req_provider_2",
                        "model": "gpt-4.1",
                        "request": {"messages": repeated_prefix + [{"role": "assistant", "content": "Done."}]},
                        "usage_raw": {
                            "cache_hit_tokens": 40,
                            "prompt_tokens_details": {"cached_tokens": 40},
                        },
                    },
                ],
            }
        ],
    )

    summary = summarize_cache_observability(workspace)

    assert summary.total_llm_calls == 2
    assert summary.calls_with_cache_usage == 2
    assert summary.total_cache_hit_tokens == 120
    assert summary.total_cache_write_tokens == 20
    assert summary.by_model == {"gpt-4.1": 2}
    assert [item.request_id for item in summary.recent_requests] == ["llm_req_2", "llm_req_1"]
    assert summary.recent_requests[0].provider_request_id == "req_provider_2"
    assert summary.prefix_candidates[0].occurrences == 2
    assert summary.prefix_candidates[0].request_ids == ("llm_req_1", "llm_req_2")
    assert "You are Aworld" in summary.prefix_candidates[0].preview


def test_summarize_cache_observability_ignores_entries_without_llm_calls(tmp_path):
    workspace = tmp_path / "workspace"
    session_log = workspace / ".aworld" / "memory" / "sessions" / "session-1.jsonl"
    _write_session_log(
        session_log,
        [
            {
                "recorded_at": "2026-05-07T00:00:00+00:00",
                "event": "task_completed",
                "task_id": "task-1",
                "final_answer": "No llm calls here.",
            }
        ],
    )

    summary = summarize_cache_observability(workspace)

    assert summary.total_llm_calls == 0
    assert summary.calls_with_cache_usage == 0
    assert summary.recent_requests == ()
    assert summary.prefix_candidates == ()


def test_summarize_cache_observability_scopes_to_task_and_session(tmp_path):
    workspace = tmp_path / "workspace"
    sessions_dir = workspace / ".aworld" / "memory" / "sessions"
    repeated_prefix = [
        {"role": "system", "content": "You are Aworld. Follow workspace guidance carefully."},
        {"role": "user", "content": "Please inspect the repo and explain the failing tests."},
    ]
    _write_session_log(
        sessions_dir / "session-1.jsonl",
        [
            {
                "recorded_at": "2026-05-07T00:00:00+00:00",
                "event": "task_completed",
                "session_id": "session-1",
                "task_id": "task-1",
                "llm_calls": [
                    {
                        "session_id": "session-1",
                        "task_id": "task-1",
                        "request_id": "llm_req_1",
                        "provider_request_id": "req_provider_1",
                        "provider_name": "openai",
                        "model": "gpt-4.1",
                        "request": {"messages": repeated_prefix},
                        "usage_raw": {"cache_hit_tokens": 80},
                    }
                ],
            }
        ],
    )
    _write_session_log(
        sessions_dir / "session-2.jsonl",
        [
            {
                "recorded_at": "2026-05-07T00:01:00+00:00",
                "event": "task_completed",
                "session_id": "session-2",
                "task_id": "task-2",
                "llm_calls": [
                    {
                        "session_id": "session-2",
                        "task_id": "task-2",
                        "request_id": "llm_req_2",
                        "provider_request_id": "req_provider_2",
                        "provider_name": "openai",
                        "model": "gpt-4.1",
                        "request": {"messages": repeated_prefix},
                        "usage_raw": {"cache_hit_tokens": 40},
                    }
                ],
            }
        ],
    )

    summary = summarize_cache_observability(
        workspace,
        task_id="task-1",
        session_id="session-1",
    )

    assert summary.total_llm_calls == 1
    assert summary.total_cache_hit_tokens == 80
    assert summary.by_model == {"gpt-4.1": 1}
    assert [item.request_id for item in summary.recent_requests] == ["llm_req_1"]


def test_summarize_cache_observability_keeps_prefix_candidates_model_specific(tmp_path):
    workspace = tmp_path / "workspace"
    session_log = workspace / ".aworld" / "memory" / "sessions" / "session-1.jsonl"
    repeated_prefix = [
        {"role": "system", "content": "You are Aworld. Follow workspace guidance carefully."},
        {"role": "user", "content": "Please inspect the repo and explain the failing tests."},
    ]
    _write_session_log(
        session_log,
        [
            {
                "recorded_at": "2026-05-07T00:00:00+00:00",
                "event": "task_completed",
                "session_id": "session-1",
                "task_id": "task-1",
                "llm_calls": [
                    {
                        "session_id": "session-1",
                        "task_id": "task-1",
                        "request_id": "llm_req_1",
                        "provider_request_id": "req_provider_1",
                        "provider_name": "openai",
                        "model": "gpt-4.1",
                        "request": {"messages": repeated_prefix},
                        "usage_raw": {"cache_hit_tokens": 80},
                    },
                    {
                        "session_id": "session-1",
                        "task_id": "task-1",
                        "request_id": "llm_req_2",
                        "provider_request_id": "req_provider_2",
                        "provider_name": "openai",
                        "model": "gpt-4.1-mini",
                        "request": {"messages": repeated_prefix},
                        "usage_raw": {"cache_hit_tokens": 40},
                    },
                ],
            }
        ],
    )

    summary = summarize_cache_observability(workspace)

    assert summary.prefix_candidates == ()
