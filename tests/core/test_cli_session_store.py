from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_session_store_records_and_lists_by_cwd(tmp_path: Path) -> None:
    from aworld_cli.core.session_store import CliSessionStore

    store = CliSessionStore(root=tmp_path)
    store.record_turn(
        session_id="session_a",
        cwd=str(tmp_path / "workspace-a"),
        agent_name="Aworld",
        mode="interactive",
        prompt="first prompt",
        task_id="task-1",
        source_type="local",
        source_location="/agents",
    )
    store.record_turn(
        session_id="session_b",
        cwd=str(tmp_path / "workspace-b"),
        agent_name="Other",
        mode="interactive",
        prompt="other prompt",
        task_id="task-2",
        source_type="local",
        source_location="/agents",
    )

    cwd_a = str((tmp_path / "workspace-a").resolve())
    records = store.list(cwd=cwd_a)

    assert [record.session_id for record in records] == ["session_a"]
    assert records[0].last_prompt == "first prompt"
    assert records[0].turn_count == 1
    assert records[0].cwd == cwd_a


def test_session_store_filters_direct_sessions_unless_requested(tmp_path: Path) -> None:
    from aworld_cli.core.session_store import CliSessionStore

    store = CliSessionStore(root=tmp_path)
    cwd = str(tmp_path.resolve())
    store.record_turn(
        session_id="session_interactive",
        cwd=cwd,
        agent_name="Aworld",
        mode="interactive",
        prompt="interactive",
        task_id=None,
        source_type=None,
        source_location=None,
    )
    store.record_turn(
        session_id="session_direct",
        cwd=cwd,
        agent_name="Aworld",
        mode="direct",
        prompt="direct",
        task_id=None,
        source_type=None,
        source_location=None,
    )

    default_records = store.list(cwd=cwd)
    all_records = store.list(cwd=cwd, include_non_interactive=True)

    assert [record.session_id for record in default_records] == ["session_interactive"]
    assert {record.session_id for record in all_records} == {
        "session_interactive",
        "session_direct",
    }


def test_session_store_latest_respects_cwd_and_all_flag(tmp_path: Path) -> None:
    from aworld_cli.core.session_store import CliSessionRecord, CliSessionStore

    store = CliSessionStore(root=tmp_path)
    store.upsert_session(
        CliSessionRecord(
            session_id="old",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            cwd=str((tmp_path / "a").resolve()),
            agent_name="Aworld",
            mode="interactive",
        )
    )
    store.upsert_session(
        CliSessionRecord(
            session_id="new-other-cwd",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-02T00:00:00",
            cwd=str((tmp_path / "b").resolve()),
            agent_name="Aworld",
            mode="interactive",
        )
    )

    assert store.latest(cwd=str((tmp_path / "a").resolve())).session_id == "old"
    assert (
        store.latest(cwd=str((tmp_path / "a").resolve()), include_all_cwds=True).session_id
        == "new-other-cwd"
    )


def test_session_store_preserves_records_created_by_another_instance(tmp_path: Path) -> None:
    from aworld_cli.core.session_store import CliSessionStore

    first = CliSessionStore(root=tmp_path)
    second = CliSessionStore(root=tmp_path)

    first.record_turn(
        session_id="session_a",
        cwd=str(tmp_path.resolve()),
        agent_name="Aworld",
        mode="interactive",
        prompt="first prompt",
        task_id="task-a",
        source_type="local",
        source_location="/agents",
    )
    second.record_turn(
        session_id="session_b",
        cwd=str(tmp_path.resolve()),
        agent_name="Aworld",
        mode="interactive",
        prompt="second prompt",
        task_id="task-b",
        source_type="local",
        source_location="/agents",
    )

    reloaded = CliSessionStore(root=tmp_path)

    assert {record.session_id for record in reloaded.list(cwd=str(tmp_path.resolve()))} == {
        "session_a",
        "session_b",
    }


def test_session_store_imports_legacy_history_when_index_missing(tmp_path: Path) -> None:
    from aworld_cli.core.session_store import CliSessionStore

    legacy_dir = tmp_path / ".aworld" / "workspaces"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / ".session_history.json"
    legacy_file.write_text(
        json.dumps(
            {
                "session_legacy": {
                    "session_id": "session_legacy",
                    "created_at": "2026-01-01T00:00:00",
                    "last_used_at": "2026-01-01T01:00:00",
                }
            }
        ),
        encoding="utf-8",
    )

    store = CliSessionStore(root=tmp_path)
    record = store.get("session_legacy")

    assert record is not None
    assert record.session_id == "session_legacy"
    assert record.mode == "interactive"
    assert record.metadata["imported_from"] == ".aworld/workspaces/.session_history.json"


def test_session_store_warns_when_context_artifacts_missing(tmp_path: Path) -> None:
    from aworld_cli.core.session_store import CliSessionRecord, CliSessionStore

    store = CliSessionStore(root=tmp_path)
    record = store.upsert_session(
        CliSessionRecord(
            session_id="session_missing_context",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            cwd=str(tmp_path.resolve()),
            agent_name="Aworld",
            mode="interactive",
            metadata={"context_artifact": ".aworld/missing.jsonl"},
        )
    )

    warning = store.context_warning(record)

    assert warning is not None
    assert "limited context" in warning


def test_session_store_warns_when_recorded_turns_have_no_memory_artifact(tmp_path: Path) -> None:
    from aworld_cli.core.session_store import CliSessionStore

    store = CliSessionStore(root=tmp_path)
    record = store.record_turn(
        session_id="session_no_memory",
        cwd=str(tmp_path.resolve()),
        agent_name="Aworld",
        mode="interactive",
        prompt="remember this",
        task_id="task-1",
        source_type="local",
        source_location="/agents",
    )

    warning = store.context_warning(record)

    assert warning is not None
    assert "limited context" in warning
    assert "session_no_memory" in warning


def test_session_store_accepts_existing_memory_artifact_for_context(tmp_path: Path) -> None:
    from aworld_cli.core.session_store import CliSessionStore

    memory_file = tmp_path / ".aworld" / "memory" / "sessions" / "session_with_memory.jsonl"
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text('{"memory_type":"message"}\n', encoding="utf-8")

    store = CliSessionStore(root=tmp_path)
    record = store.record_turn(
        session_id="session_with_memory",
        cwd=str(tmp_path.resolve()),
        agent_name="Aworld",
        mode="interactive",
        prompt="remember this",
        task_id="task-1",
        source_type="local",
        source_location="/agents",
    )

    assert store.context_warning(record) is None


def test_session_store_accepts_runtime_memory_root_artifact(monkeypatch, tmp_path: Path) -> None:
    from aworld_cli.core.session_store import CliSessionStore

    runtime_memory = tmp_path / "runtime-memory"
    memory_file = runtime_memory / "sessions" / "session_runtime_memory.jsonl"
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text('{"memory_type":"message"}\n', encoding="utf-8")
    monkeypatch.setenv("AWORLD_MEMORY_ROOT", str(runtime_memory))

    store = CliSessionStore(root=tmp_path / "workspace")
    record = store.record_turn(
        session_id="session_runtime_memory",
        cwd=str((tmp_path / "workspace").resolve()),
        agent_name="Aworld",
        mode="interactive",
        prompt="remember this",
        task_id="task-1",
        source_type="local",
        source_location="/agents",
    )

    assert store.context_warning(record) is None
