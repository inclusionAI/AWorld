from pathlib import Path
from types import SimpleNamespace

import pytest

from aworld.core.context.base import Context
from aworld.core.context.compiler import CacheBreakReason
from aworld_cli.core import context as context_module


class _MemoryWithSummary:
    def __init__(self) -> None:
        self.saved = []

    def get_all(self, *, filters):
        if filters.get("memory_type") == "summary":
            return [SimpleNamespace(content="bounded continuation")]
        return [SimpleNamespace(content="old history")]

    async def _run_summary_in_background(self, **_kwargs):
        return None

    async def _add(self, *, memory_item, **_kwargs):
        self.saved.append(memory_item)


@pytest.mark.asyncio
async def test_manual_cli_compaction_creates_one_provider_neutral_cache_epoch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    memory = _MemoryWithSummary()
    monkeypatch.setattr(context_module.MemoryFactory, "instance", lambda: memory)
    monkeypatch.setattr(
        context_module,
        "get_default_history_path",
        lambda: tmp_path / "missing-history.jsonl",
    )
    monkeypatch.setattr(
        context_module,
        "extract_file_context",
        lambda _path: "workspace evidence",
    )
    context = Context(
        task_id="cli-compaction-task",
        session=SimpleNamespace(session_id="cli-compaction-session"),
    )

    result = await context_module.run_context_optimization(
        agent_id="agent",
        context=context,
    )

    assert result[0] is True
    assert len(memory.saved) == 1
    assert context.context_lifecycle_state.checkpoint_revision == 1
    assert context.get_pending_cache_break_reasons() == (
        CacheBreakReason.HISTORY_COMPACTION,
    )
    state = context.context_info["cli_context_compaction_state"]
    assert state["checkpoint_snapshot_state"] == "captured"
    assert isinstance(state["checkpoint_id"], str)
