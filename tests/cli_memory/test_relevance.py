import json
from pathlib import Path

from aworld_cli.memory.durable import append_durable_memory_record, durable_memory_file
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


def test_relevant_memory_context_prefers_higher_confidence_candidates_over_low_confidence_noise(
    tmp_path,
) -> None:
    workspace = tmp_path / "workspace"
    sessions_dir = workspace / ".aworld" / "memory" / "sessions"

    _write_session_log(
        sessions_dir / "session-1.jsonl",
        [
            {
                "recorded_at": "2026-04-29T10:00:00+00:00",
                "session_id": "session-1",
                "task_id": "task-1",
                "final_answer": "Use pnpm for workspace package management.",
                "candidates": [
                    {
                        "memory_type": "workspace",
                        "content": "Use pnpm for workspace package management.",
                        "confidence": "medium",
                        "promotion": "session_log_only",
                    }
                ],
            },
            {
                "recorded_at": "2026-04-29T10:05:00+00:00",
                "session_id": "session-1",
                "task_id": "task-2",
                "final_answer": "Temporary pnpm package management tests workspace note for current task only.",
                "candidates": [
                    {
                        "memory_type": "workspace",
                        "content": "Temporary pnpm package management tests workspace note for current task only.",
                        "confidence": "low",
                        "promotion": "session_log_only",
                    }
                ],
            },
        ],
    )

    provider = CliDurableMemoryProvider()
    context = provider.get_relevant_memory_context(
        workspace_path=str(workspace),
        query="Need pnpm package management guidance",
        limit=1,
    )

    assert context.texts == ("Use pnpm for workspace package management.",)


def test_relevant_memory_context_prioritizes_auto_promoted_candidates(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    sessions_dir = workspace / ".aworld" / "memory" / "sessions"

    _write_session_log(
        sessions_dir / "session-1.jsonl",
        [
            {
                "recorded_at": "2026-04-29T10:00:00+00:00",
                "session_id": "session-1",
                "task_id": "task-1",
                "final_answer": "Use pnpm for workspace package management, tests, CI, lint, and format checks.",
                "candidates": [
                    {
                        "memory_type": "workspace",
                        "content": "Use pnpm for workspace package management, tests, CI, lint, and format checks.",
                        "confidence": "medium",
                        "promotion": "session_log_only",
                    }
                ],
            },
            {
                "recorded_at": "2026-04-29T10:10:00+00:00",
                "session_id": "session-1",
                "task_id": "task-2",
                "final_answer": "Always use pnpm for workspace package management and never run npm install here.",
                "candidates": [
                    {
                        "memory_type": "workspace",
                        "content": "Always use pnpm for workspace package management and never run npm install here.",
                        "confidence": "high",
                        "promotion": "durable_memory",
                    }
                ],
            },
        ],
    )

    provider = CliDurableMemoryProvider()
    context = provider.get_relevant_memory_context(
        workspace_path=str(workspace),
        query="Need pnpm package management guidance",
        limit=1,
    )

    assert context.texts == (
        "Always use pnpm for workspace package management and never run npm install here.",
    )


def test_relevant_memory_context_ranks_mixed_sources_globally(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    sessions_dir = workspace / ".aworld" / "memory" / "sessions"

    append_durable_memory_record(
        workspace,
        memory_type="workspace",
        memory_kind="fact",
        text="Use pnpm here.",
        source="remember_command",
    )
    _write_session_log(
        sessions_dir / "session-1.jsonl",
        [
            {
                "recorded_at": "2026-04-29T10:10:00+00:00",
                "session_id": "session-1",
                "task_id": "task-1",
                "final_answer": "Use pnpm for workspace package management and keep CI checks fast.",
                "candidates": [
                    {
                        "memory_type": "workspace",
                        "content": "Use pnpm for workspace package management and keep CI checks fast.",
                        "confidence": "high",
                        "promotion": "session_log_only",
                    }
                ],
            },
        ],
    )

    provider = CliDurableMemoryProvider()
    context = provider.get_relevant_memory_context(
        workspace_path=str(workspace),
        query="Need pnpm package management and CI guidance",
        limit=1,
    )

    assert context.texts == (
        "Use pnpm for workspace package management and keep CI checks fast.",
    )
    assert context.source_files == (sessions_dir / "session-1.jsonl",)


def test_relevant_memory_context_source_files_match_only_returned_texts(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    sessions_dir = workspace / ".aworld" / "memory" / "sessions"

    append_durable_memory_record(
        workspace,
        memory_type="workspace",
        memory_kind="fact",
        text="The release branch is cut from main every Thursday.",
        source="remember_command",
    )
    _write_session_log(
        sessions_dir / "session-1.jsonl",
        [
            {
                "recorded_at": "2026-04-29T10:10:00+00:00",
                "session_id": "session-1",
                "task_id": "task-1",
                "final_answer": "The release checklist lives in docs/releases.md on main.",
                "candidates": [
                    {
                        "memory_type": "workspace",
                        "content": "The release checklist lives in docs/releases.md on main.",
                        "confidence": "high",
                        "promotion": "session_log_only",
                    }
                ],
            },
        ],
    )
    _write_session_log(
        sessions_dir / "session-2.jsonl",
        [
            {
                "recorded_at": "2026-04-29T10:05:00+00:00",
                "session_id": "session-2",
                "task_id": "task-2",
                "final_answer": "Release notes are announced in Slack after the cut.",
                "candidates": [
                    {
                        "memory_type": "workspace",
                        "content": "Release notes are announced in Slack after the cut.",
                        "confidence": "medium",
                        "promotion": "session_log_only",
                    }
                ],
            },
        ],
    )

    provider = CliDurableMemoryProvider()
    context = provider.get_relevant_memory_context(
        workspace_path=str(workspace),
        query="Where is the release checklist on main and what branch gets cut every Thursday?",
        limit=2,
    )

    assert context.texts == (
        "The release branch is cut from main every Thursday.",
        "The release checklist lives in docs/releases.md on main.",
    )
    assert context.source_files == (
        durable_memory_file(workspace),
        sessions_dir / "session-1.jsonl",
    )
