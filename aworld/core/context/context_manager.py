import time
from typing import Optional

from aworld.core.context.base import Context
from aworld.checkpoint import BaseCheckpointRepository, create_checkpoint, CheckpointMetadata, Checkpoint
from aworld.checkpoint.inmemory import InMemoryCheckpointRepository
from aworld.core.context.state.agent_state import BaseAgentState
from aworld.logs.util import logger

class ContextManager:

    checkpoint_repo: BaseCheckpointRepository

    def __init__(self, checkpoint_repo: Optional[InMemoryCheckpointRepository] = None):
        self.checkpoint_repo = checkpoint_repo or InMemoryCheckpointRepository()

    async def save(self, context: Context, **kwargs) -> Checkpoint:
        task = context.get_task()
        session_id = task.session_id

        # Use new Context functionality to create complete session state snapshot
        values = {"context": context.model_dump()}
        
        # Add additional parameters
        values.update(kwargs)
        
        metadata = CheckpointMetadata(
            session_id=session_id,
            task_id=task.id
        )

        checkpoint = create_checkpoint(values=values, metadata=metadata)
        self.checkpoint_repo.put(checkpoint)

        logger.info(f"[ContextManager] Complete checkpoint saved for session {metadata.session_id}, task {metadata.task_id}")
        
        return checkpoint

    async def reload(self, session_id: str) -> Context:
        # Query checkpoint
        checkpoint = self.checkpoint_repo.get_by_session(session_id)
        if not checkpoint:
            logger.warning(f"[ContextManager] No checkpoint found for session {session_id}")
            return None
        
        logger.info(f"[ContextManager] Found checkpoint for session {session_id}, task {checkpoint.metadata.task_id}")
        
        # Restore Context from checkpoint
        context = Context(**checkpoint.values["context"])
        
        logger.info(f"[ContextManager] Successfully reloaded context for session {session_id} {context}")
        return context

    def deep_copy(self, context: Context) -> Context:
        return context.deep_copy()

    def merge_context(self, context: Context, other_context: Context) -> None:
        context.merge_context(other_context)

    def merge_state(self, context: Context, state: BaseAgentState) -> None:
        if not isinstance(state, BaseAgentState):
            logger.warning(f"[ContextManager] Invalid state type: {type(state)}, expected BaseAgentState")
            return

        try:
            # Merge memory_messages (short-term memory)
            if state.memory_messages:
                existing_messages = context.context_info.get('memory_messages', [])
                if isinstance(existing_messages, list):
                    # Extend existing messages with new ones
                    existing_messages.extend(state.memory_messages)
                    context.context_info['memory_messages'] = existing_messages
                else:
                    # Replace if not a list
                    context.context_info['memory_messages'] = state.memory_messages
                logger.debug(f"[ContextManager] Merged {len(state.memory_messages)} memory messages")

            # Merge artifacts (AIGC artifacts)
            if state.artifacts:
                existing_artifacts = context.context_info.get('artifacts', [])
                if isinstance(existing_artifacts, list):
                    # Extend existing artifacts with new ones
                    existing_artifacts.extend(state.artifacts)
                    context.context_info['artifacts'] = existing_artifacts
                else:
                    # Replace if not a list
                    context.context_info['artifacts'] = state.artifacts
                logger.debug(f"[ContextManager] Merged {len(state.artifacts)} artifacts")

            # Merge kv_store (custom key-value store)
            if state.kv_store:
                existing_kv_store = context.context_info.get('kv_store', {})
                if isinstance(existing_kv_store, dict):
                    # Update existing kv_store with new key-value pairs
                    existing_kv_store.update(state.kv_store)
                    context.context_info['kv_store'] = existing_kv_store
                else:
                    # Replace if not a dict
                    context.context_info['kv_store'] = state.kv_store.copy()
                logger.debug(f"[ContextManager] Merged {len(state.kv_store)} kv_store entries")

            # Merge context_usage if available
            if state.context_usage:
                existing_usage = context.context_info.get('context_usage')
                if existing_usage:
                    # Merge usage statistics
                    if hasattr(existing_usage, 'total_context_length'):
                        existing_usage.total_context_length = max(
                            existing_usage.total_context_length,
                            state.context_usage.total_context_length
                        )
                    if hasattr(existing_usage, 'used_context_length'):
                        existing_usage.used_context_length += state.context_usage.used_context_length
                else:
                    context.context_info['context_usage'] = state.context_usage
                logger.debug(f"[ContextManager] Merged context usage information")

            # Merge context_rule if available
            if state.context_rule:
                context.context_info['context_rule'] = state.context_rule
                logger.debug(f"[ContextManager] Merged context rule")

            logger.info(f"[ContextManager] Successfully merged BaseAgentState into context")

        except Exception as e:
            logger.error(f"[ContextManager] Failed to merge state: {e}")
            raise

    def get_checkpoint(self, session_id: str) -> Optional[Checkpoint]:
        return self.checkpoint_repo.get_by_session(session_id)
    
    def delete_checkpoint(self, session_id: str) -> None:
        self.checkpoint_repo.delete_by_session(session_id)
        logger.info(f"[ContextManager] Deleted checkpoint for session {session_id}")
    
    def list_checkpoints(self, **params) -> list:
        return self.checkpoint_repo.list(params)




