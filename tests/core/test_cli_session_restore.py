from __future__ import annotations

from pathlib import Path

import pytest


def test_restore_session_to_executor_updates_runtime_and_store(tmp_path: Path) -> None:
    from aworld_cli.core.session_restore import restore_session_to_executor
    from aworld_cli.core.session_store import CliSessionRecord, CliSessionStore

    store = CliSessionStore(root=tmp_path)
    record = store.upsert_session(
        CliSessionRecord(
            session_id="session_restore_core",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            cwd=str(tmp_path.resolve()),
            agent_name="Aworld",
            mode="interactive",
            metadata={"context_artifact": ".aworld/missing-context.jsonl"},
        )
    )

    class FakeRuntime:
        def __init__(self):
            self.reset_session = None

        def reset_hud_session(self, session_id):
            self.reset_session = session_id

    class FakeExecutor:
        def __init__(self):
            self.session_id = "old_session"
            self._base_runtime = FakeRuntime()
            self.tool_logging_restarted = False

        def _start_tool_logging(self):
            self.tool_logging_restarted = True

    executor = FakeExecutor()

    result = restore_session_to_executor(
        record=record,
        executor_instance=executor,
        session_store=store,
        current_agent_name="Aworld",
    )

    assert executor.session_id == "session_restore_core"
    assert executor.tool_logging_restarted is True
    assert executor._base_runtime.reset_session == "session_restore_core"
    assert store.get("session_restore_core").updated_at != "2026-01-01T00:00:00+00:00"
    assert result.warning is not None
    assert "limited context" in result.warning


def test_restore_session_to_executor_warns_for_explicit_cwd_mismatch(tmp_path: Path) -> None:
    from aworld_cli.core.session_restore import restore_session_to_executor
    from aworld_cli.core.session_store import CliSessionRecord, CliSessionStore

    store = CliSessionStore(root=tmp_path)
    record = store.upsert_session(
        CliSessionRecord(
            session_id="session_other_cwd",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            cwd=str((tmp_path / "other").resolve()),
            agent_name="Aworld",
            mode="interactive",
        )
    )
    executor = type("Executor", (), {"session_id": "old"})()

    result = restore_session_to_executor(
        record=record,
        executor_instance=executor,
        session_store=store,
        current_agent_name="Aworld",
        current_cwd=str((tmp_path / "current").resolve()),
    )

    assert executor.session_id == "session_other_cwd"
    assert result.warning is not None
    assert "belongs to" in result.warning


def test_resolve_session_record_uses_latest_filters(tmp_path: Path) -> None:
    from aworld_cli.core.session_restore import resolve_session_record
    from aworld_cli.core.session_store import CliSessionRecord, CliSessionStore

    store = CliSessionStore(root=tmp_path)
    current_cwd = str((tmp_path / "current").resolve())
    other_cwd = str((tmp_path / "other").resolve())
    store.upsert_session(
        CliSessionRecord(
            session_id="session_current",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            cwd=current_cwd,
            agent_name="Aworld",
            mode="interactive",
        )
    )
    store.upsert_session(
        CliSessionRecord(
            session_id="session_other_latest",
            created_at="2026-01-02T00:00:00+00:00",
            updated_at="2026-01-02T00:00:00+00:00",
            cwd=other_cwd,
            agent_name="Aworld",
            mode="interactive",
        )
    )

    assert (
        resolve_session_record(
            session_store=store,
            session_id=None,
            cwd=current_cwd,
            use_latest=True,
        ).session_id
        == "session_current"
    )
    assert (
        resolve_session_record(
            session_store=store,
            session_id=None,
            cwd=current_cwd,
            use_latest=True,
            include_all_cwds=True,
        ).session_id
        == "session_other_latest"
    )


def test_cli_runtime_restores_executor_with_shared_core(monkeypatch, tmp_path: Path) -> None:
    from aworld_cli.core.session_store import CliSessionRecord, CliSessionStore
    from aworld_cli.runtime.cli import CliRuntime

    store = CliSessionStore(root=tmp_path)
    record = store.upsert_session(
        CliSessionRecord(
            session_id="session_runtime_restore",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            cwd=str(tmp_path.resolve()),
            agent_name="Aworld",
            mode="interactive",
        )
    )

    calls = {}

    def fake_restore_session_to_executor(**kwargs):
        calls.update(kwargs)
        kwargs["executor_instance"].session_id = kwargs["record"].session_id
        return type(
            "Result",
            (),
            {
                "record": record,
                "message": "Restored to session: session_runtime_restore",
                "warning": None,
            },
        )()

    monkeypatch.setattr(
        "aworld_cli.runtime.cli.restore_session_to_executor",
        fake_restore_session_to_executor,
    )

    executor = type("Executor", (), {"session_id": "old_session"})()
    runtime = CliRuntime(
        agent_name="Aworld",
        session_id=record.session_id,
        resume_record=record,
        session_store=store,
    )

    runtime._restore_executor_session(executor, current_agent_name="Aworld")

    assert executor.session_id == "session_runtime_restore"
    assert calls["record"] is record
    assert calls["executor_instance"] is executor
    assert calls["session_store"] is store


def test_cli_runtime_can_allow_agent_override_for_resume(monkeypatch, tmp_path: Path) -> None:
    from aworld_cli.core.session_store import CliSessionRecord, CliSessionStore
    from aworld_cli.runtime.cli import CliRuntime

    store = CliSessionStore(root=tmp_path)
    record = store.upsert_session(
        CliSessionRecord(
            session_id="session_agent_override",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            cwd=str(tmp_path.resolve()),
            agent_name="Original",
            mode="interactive",
        )
    )
    calls = {}

    def fake_restore_session_to_executor(**kwargs):
        calls.update(kwargs)
        return type("Result", (), {"record": record, "message": "ok", "warning": None})()

    monkeypatch.setattr(
        "aworld_cli.runtime.cli.restore_session_to_executor",
        fake_restore_session_to_executor,
    )

    runtime = CliRuntime(
        agent_name="Override",
        session_id=record.session_id,
        resume_record=record,
        session_store=store,
        require_same_resume_agent=False,
        resume_cwd=str(tmp_path.resolve()),
    )

    runtime._restore_executor_session(type("Executor", (), {"session_id": "old"})(), current_agent_name="Override")

    assert calls["require_same_agent"] is False
    assert calls["current_cwd"] == str(tmp_path.resolve())


def test_cli_runtime_annotates_executor_source_metadata() -> None:
    from aworld_cli.runtime.cli import CliRuntime

    runtime = CliRuntime(agent_name="Aworld")
    executor = type("Executor", (), {})()

    runtime._annotate_executor_source(
        executor,
        {"type": "local", "location": "/agents"},
    )

    assert executor._session_source_type == "local"
    assert executor._session_source_location == "/agents"


def test_cli_runtime_ensures_session_record_for_new_executor(monkeypatch, tmp_path: Path) -> None:
    from aworld_cli.core.session_store import CliSessionStore
    from aworld_cli.runtime.cli import CliRuntime

    monkeypatch.setenv("AWORLD_CLI_SESSION_STORE_ROOT", str(tmp_path))
    executor = type(
        "Executor",
        (),
        {
            "session_id": "session_new_executor",
            "_session_mode": "interactive",
            "_session_source_type": "local",
            "_session_source_location": "/agents",
        },
    )()
    runtime = CliRuntime(agent_name="Aworld")

    runtime._restore_executor_session(executor, current_agent_name="Aworld")

    record = CliSessionStore(root=tmp_path).get("session_new_executor")
    assert record is not None
    assert record.agent_name == "Aworld"
    assert record.source_type == "local"
    assert record.source_location == "/agents"


@pytest.mark.asyncio
async def test_cli_runtime_fails_resume_when_stored_agent_unavailable(capsys) -> None:
    from aworld_cli.models import AgentInfo
    from aworld_cli.runtime.cli import CliRuntime

    runtime = CliRuntime(agent_name="MissingAgent", fail_on_missing_agent=True)

    selected = await runtime._select_agent(
        [AgentInfo(name="Aworld", desc="", source_type="local", source_location=".")]
    )

    output = capsys.readouterr().out
    assert selected is None
    assert "MissingAgent" in output
    assert "--agent" in output


@pytest.mark.asyncio
async def test_resume_context_uses_same_session_memory_path(monkeypatch, tmp_path: Path) -> None:
    from aworld.core.memory import MemoryConfig
    from aworld.memory.db.filesystem import FileSystemMemoryStore
    from aworld.memory.main import AworldMemory
    from aworld.memory.models import MemoryHumanMessage, MessageMetadata
    from aworld_cli.core.session_restore import restore_session_to_executor
    from aworld_cli.core.session_store import CliSessionRecord, CliSessionStore

    memory_root = tmp_path / "runtime-memory"
    monkeypatch.setenv("AWORLD_MEMORY_ROOT", str(memory_root))

    memory = AworldMemory(
        memory_store=FileSystemMemoryStore(memory_root=str(memory_root)),
        config=MemoryConfig(provider="aworld"),
    )
    await memory.add(
        MemoryHumanMessage(
            content="previous turn content",
            metadata=MessageMetadata(
                agent_id="Aworld",
                agent_name="Aworld",
                session_id="session_continuity",
                task_id="task-1",
                user_id="user",
            ),
        )
    )

    store = CliSessionStore(root=tmp_path / "workspace")
    record = store.upsert_session(
        CliSessionRecord(
            session_id="session_continuity",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            cwd=str((tmp_path / "workspace").resolve()),
            agent_name="Aworld",
            mode="interactive",
            turn_count=1,
        )
    )
    executor = type("Executor", (), {"session_id": "old"})()

    result = restore_session_to_executor(
        record=record,
        executor_instance=executor,
        session_store=store,
        current_agent_name="Aworld",
    )

    items = memory.get_last_n(10, filters={"session_id": "session_continuity"})
    assert result.warning is None
    assert executor.session_id == "session_continuity"
    assert [item.content for item in items] == ["previous turn content"]
