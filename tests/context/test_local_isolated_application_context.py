from __future__ import annotations

from aworld.checkpoint.inmemory import InMemoryCheckpointRepository
from aworld.core.context.amni.local import LocalIsolatedApplicationContext
from aworld.core.context.amni.config import AmniConfigFactory
from aworld.memory.main import InMemoryMemoryStore


def test_local_context_owns_ephemeral_resources() -> None:
    context = LocalIsolatedApplicationContext.create(
        task_id="candidate-task",
        task_content="generate candidate",
    )

    assert context.execution_scope == "self_evolve"
    assert context.context_info["execution_scope"] == "self_evolve"
    assert isinstance(context.local_memory.memory_store, InMemoryMemoryStore)
    assert isinstance(context.checkpoint_repository, InMemoryCheckpointRepository)
    assert context.workspace is None
    assert context.get_config().agent_config.enable_summary is False
    assert context.get_config().agent_config.history_scope == "task"


def test_local_context_instances_do_not_share_memory_or_checkpoints() -> None:
    first = LocalIsolatedApplicationContext.create(
        task_id="candidate-0",
        task_content="first",
    )
    second = LocalIsolatedApplicationContext.create(
        task_id="candidate-1",
        task_content="second",
    )

    assert first.local_memory is not second.local_memory
    assert first.local_memory.memory_store is not second.local_memory.memory_store
    assert first.checkpoint_repository is not second.checkpoint_repository


def test_local_context_does_not_mutate_explicit_config() -> None:
    config = AmniConfigFactory.create()
    config.agent_config.enable_summary = True
    config.agent_config.history_scope = "session"

    context = LocalIsolatedApplicationContext.create(
        task_id="candidate-config",
        task_content="isolated config",
        context_config=config,
    )

    assert context.get_config().agent_config.enable_summary is False
    assert context.get_config().agent_config.history_scope == "task"
    assert config.agent_config.enable_summary is True
    assert config.agent_config.history_scope == "session"
