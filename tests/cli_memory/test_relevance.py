import json
from pathlib import Path

from aworld_cli.memory.provider import CliDurableMemoryProvider


def _write_session_log(path: Path, payloads: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(payload, ensure_ascii=False) for payload in payloads) + "\n",
        encoding="utf-8",
    )


def test_relevant_memory_context_selects_matching_session_logs_only(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    sessions_dir = workspace / ".aworld" / "memory" / "sessions"

    _write_session_log(
        sessions_dir / "session-1.jsonl",
        [
            {
                "recorded_at": "2026-04-29T10:00:00+00:00",
                "session_id": "session-1",
                "task_id": "task-1",
                "final_answer": "Use pnpm and keep tests fast in this workspace.",
                "candidates": [
                    {
                        "memory_type": "workspace",
                        "content": "Use pnpm and keep tests fast in this workspace.",
                    }
                ],
            },
            {
                "recorded_at": "2026-04-29T11:00:00+00:00",
                "session_id": "session-1",
                "task_id": "task-2",
                "final_answer": "Prefer poetry for package management in a different repo.",
                "candidates": [
                    {
                        "memory_type": "reference",
                        "content": "Prefer poetry for package management in a different repo.",
                    }
                ],
            },
        ],
    )

    provider = CliDurableMemoryProvider()
    context = provider.get_relevant_memory_context(
        workspace_path=str(workspace),
        query="How should I manage packages and tests with pnpm here?",
    )

    assert context.texts == ("Use pnpm and keep tests fast in this workspace.",)


def test_relevant_memory_context_limits_results_by_relevance_order(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    sessions_dir = workspace / ".aworld" / "memory" / "sessions"

    _write_session_log(
        sessions_dir / "session-1.jsonl",
        [
            {
                "recorded_at": "2026-04-29T10:00:00+00:00",
                "session_id": "session-1",
                "task_id": "task-1",
                "final_answer": "Use pnpm and keep tests fast.",
                "candidates": [{"memory_type": "workspace", "content": "Use pnpm and keep tests fast."}],
            },
            {
                "recorded_at": "2026-04-29T10:05:00+00:00",
                "session_id": "session-1",
                "task_id": "task-2",
                "final_answer": "Keep eslint and prettier checks in CI.",
                "candidates": [{"memory_type": "workspace", "content": "Keep eslint and prettier checks in CI."}],
            },
            {
                "recorded_at": "2026-04-29T10:10:00+00:00",
                "session_id": "session-1",
                "task_id": "task-3",
                "final_answer": "Document release steps in CHANGELOG.md.",
                "candidates": [{"memory_type": "reference", "content": "Document release steps in CHANGELOG.md."}],
            },
        ],
    )

    provider = CliDurableMemoryProvider()
    context = provider.get_relevant_memory_context(
        workspace_path=str(workspace),
        query="Need pnpm package tests guidance plus CI checks",
        limit=2,
    )

    assert context.texts == (
        "Use pnpm and keep tests fast.",
        "Keep eslint and prettier checks in CI.",
    )
