from pathlib import Path

from aworld_cli.memory.provider import CliDurableMemoryProvider


def test_provider_lists_explicit_durable_records_by_type(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    provider = CliDurableMemoryProvider()

    result = provider.append_durable_memory_record(
        workspace_path=workspace,
        text="Use pnpm for workspace package management",
        memory_type="workspace",
        source="remember_command",
    )

    assert result.record_created is True
    assert result.record_path == workspace / ".aworld" / "memory" / "durable.jsonl"
    assert result.instruction_target == workspace / ".aworld" / "AWORLD.md"

    records = provider.get_durable_memory_records(workspace)
    assert len(records) == 1
    assert records[0].memory_type == "workspace"
    assert records[0].content == "Use pnpm for workspace package management"
    assert records[0].source == "remember_command"

    workspace_records = provider.get_durable_memory_records(
        workspace,
        memory_type="workspace",
    )
    assert workspace_records == records

    assert provider.get_durable_memory_records(workspace, memory_type="reference") == ()
