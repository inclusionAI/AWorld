from __future__ import annotations

from typing import Any

from aworld.checkpoint.inmemory import InMemoryCheckpointRepository
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni.config import AmniConfigFactory, AmniContextConfig
from aworld.core.memory import MemoryBase, MemoryConfig
from aworld.memory.main import InMemoryMemoryStore, MemoryFactory


class LocalIsolatedApplicationContext(ApplicationContext):
    """ApplicationContext with resources owned by one local execution."""

    local_memory: MemoryBase
    execution_scope: str

    @classmethod
    def create(
        cls,
        user_id: str = "user",
        session_id: str | None = None,
        task_id: str | None = None,
        task_content: str = "",
        context_config: AmniContextConfig | None = None,
        parent: ApplicationContext | None = None,
        *,
        memory: MemoryBase | None = None,
        execution_scope: str = "self_evolve",
        **kwargs: Any,
    ) -> "LocalIsolatedApplicationContext":
        config = (
            context_config.model_copy(deep=True)
            if context_config is not None
            else AmniConfigFactory.create()
        )
        config.agent_config.enable_summary = False
        config.agent_config.history_scope = "task"
        owned_memory = memory or MemoryFactory.from_config(
            config=MemoryConfig(provider="aworld"),
            memory_store=InMemoryMemoryStore(),
        )
        context = super().create(
            user_id=user_id,
            session_id=session_id,
            task_id=task_id,
            task_content=task_content,
            context_config=config,
            parent=parent,
            **kwargs,
        )
        context.local_memory = owned_memory
        context.execution_scope = execution_scope
        context.context_info["execution_scope"] = execution_scope
        context.checkpoint_repository = InMemoryCheckpointRepository()
        context.workspace = None
        return context

    def create_isolated_sibling(
        self,
        *,
        task_id: str,
        task_content: str,
    ) -> "LocalIsolatedApplicationContext":
        return type(self).create(
            user_id=self.task_input.user_id,
            session_id=self.session_id,
            task_id=task_id,
            task_content=task_content,
            context_config=self.get_config().model_copy(deep=True),
            parent=self.parent,
            execution_scope=self.execution_scope,
        )
