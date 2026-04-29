from __future__ import annotations

from pathlib import Path
from typing import Optional

from aworld.config import AgentMemoryConfig
from aworld.core.memory import MemoryBase, MemoryConfig, MemoryItem, MemoryStore
from aworld.memory.main import AworldMemory
from aworld.memory.models import (
    AgentExperience,
    Fact,
    LongTermMemoryTriggerParams,
    UserProfile,
)
from aworld_cli.memory.provider import (
    CliDurableMemoryProvider,
    InstructionContext,
    RelevantMemoryContext,
)


class HybridMemoryProvider(MemoryBase):
    def __init__(
        self,
        runtime_memory: AworldMemory,
        durable_provider: CliDurableMemoryProvider,
        config: MemoryConfig,
    ) -> None:
        self.runtime_memory = runtime_memory
        self.durable_provider = durable_provider
        self.config = config
        self.memory_store = runtime_memory.memory_store

    def get_instruction_layers(self, workspace_path: str | Path | None = None):
        return self.durable_provider.get_instruction_layers(workspace_path=workspace_path)

    def get_instruction_context(
        self,
        workspace_path: str | Path | None = None,
    ) -> InstructionContext:
        return self.durable_provider.get_instruction_context(workspace_path=workspace_path)

    def get_relevant_memory_context(
        self,
        workspace_path: str | Path | None = None,
        query: str = "",
        limit: int = 3,
    ) -> RelevantMemoryContext:
        return self.durable_provider.get_relevant_memory_context(
            workspace_path=workspace_path,
            query=query,
            limit=limit,
        )

    def get(self, memory_id) -> Optional[MemoryItem]:
        return self.runtime_memory.get(memory_id)

    def get_all(self, filters: dict = None) -> Optional[list[MemoryItem]]:
        return self.runtime_memory.get_all(filters=filters)

    def get_last_n(
        self,
        last_rounds,
        filters: dict = None,
        agent_memory_config: AgentMemoryConfig = None,
    ) -> Optional[list[MemoryItem]]:
        return self.runtime_memory.get_last_n(
            last_rounds,
            filters=filters,
            agent_memory_config=agent_memory_config,
        )

    async def trigger_short_term_memory_to_long_term(
        self,
        params: LongTermMemoryTriggerParams,
        agent_memory_config: AgentMemoryConfig = None,
    ):
        return await self.runtime_memory.trigger_short_term_memory_to_long_term(
            params,
            agent_memory_config=agent_memory_config,
        )

    async def retrival_user_profile(
        self,
        user_id: str,
        user_input: str,
        threshold: float = 0.5,
        limit: int = 3,
        filters: dict = None,
    ) -> Optional[list[UserProfile]]:
        return await self.runtime_memory.retrival_user_profile(
            user_id,
            user_input,
            threshold=threshold,
            limit=limit,
            filters=filters,
        )

    async def retrival_facts(
        self,
        user_id: str,
        user_input: str,
        threshold: float = 0.5,
        limit: int = 3,
        filters: dict = None,
    ) -> Optional[list[Fact]]:
        return await self.runtime_memory.retrival_facts(
            user_id,
            user_input,
            threshold=threshold,
            limit=limit,
            filters=filters,
        )

    async def retrival_agent_experience(
        self,
        agent_id: str,
        user_input: str,
        threshold: float = 0.5,
        limit: int = 3,
        filters: dict = None,
    ) -> Optional[list[AgentExperience]]:
        return await self.runtime_memory.retrival_agent_experience(
            agent_id,
            user_input,
            threshold=threshold,
            limit=limit,
            filters=filters,
        )

    async def retrival_similar_user_messages_history(
        self,
        user_id: str,
        user_input: str,
        threshold: float = 0.5,
        limit: int = 10,
        filters: dict = None,
    ) -> Optional[list[MemoryItem]]:
        return await self.runtime_memory.retrival_similar_user_messages_history(
            user_id,
            user_input,
            threshold=threshold,
            limit=limit,
            filters=filters,
        )

    def search(
        self,
        query,
        limit=100,
        memory_type="message",
        threshold=0.8,
        filters=None,
    ) -> Optional[list[MemoryItem]]:
        return self.runtime_memory.search(
            query,
            limit=limit,
            memory_type=memory_type,
            threshold=threshold,
            filters=filters,
        )

    async def add(
        self,
        memory_item: MemoryItem,
        filters: dict = None,
        agent_memory_config: AgentMemoryConfig = None,
    ):
        return await self.runtime_memory.add(
            memory_item,
            filters=filters,
            agent_memory_config=agent_memory_config,
        )

    def update(self, memory_item: MemoryItem):
        return self.runtime_memory.update(memory_item)

    async def async_gen_cur_round_summary(
        self,
        to_be_summary: MemoryItem,
        filters: dict,
        last_rounds: int,
        agent_memory_config: AgentMemoryConfig,
    ) -> str:
        return await self.runtime_memory.async_gen_cur_round_summary(
            to_be_summary,
            filters,
            last_rounds,
            agent_memory_config,
        )

    async def async_gen_multi_rounds_summary(
        self,
        to_be_summary: list[MemoryItem],
        agent_memory_config: AgentMemoryConfig,
    ) -> str:
        return await self.runtime_memory.async_gen_multi_rounds_summary(
            to_be_summary,
            agent_memory_config,
        )

    async def async_gen_summary(
        self,
        filters: dict,
        last_rounds: int,
        agent_memory_config: AgentMemoryConfig,
    ) -> str:
        return await self.runtime_memory.async_gen_summary(
            filters,
            last_rounds,
            agent_memory_config,
        )

    def delete(self, memory_id):
        return self.runtime_memory.delete(memory_id)

    def delete_items(
        self,
        message_types: list[str],
        session_id: str,
        task_id: str,
        filters: dict = None,
    ):
        return self.runtime_memory.delete_items(
            message_types,
            session_id,
            task_id,
            filters=filters,
        )

    def __getattr__(self, name: str):
        return getattr(self.runtime_memory, name)


def build_hybrid_memory_provider(
    config: MemoryConfig,
    memory_store: MemoryStore,
) -> HybridMemoryProvider:
    runtime_config = config.model_copy(update={"provider": "aworld"})
    runtime_memory = AworldMemory(memory_store=memory_store, config=runtime_config)
    durable_provider = CliDurableMemoryProvider()
    return HybridMemoryProvider(
        runtime_memory=runtime_memory,
        durable_provider=durable_provider,
        config=config,
    )
