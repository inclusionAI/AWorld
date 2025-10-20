import asyncio
import copy
import datetime
import logging
import traceback
from typing import Optional

from pydantic import BaseModel, ConfigDict
from .logger import logger
from .checkpoint.workspace import WorkspaceCheckpointRepository, CheckpointArtifact
from .prompt.formatter.task_formatter import TaskFormatter
from .prompt.prompts import AMNI_CONTEXT_PROMPT
from .state import TaskInput, Summary
from .utils import jsonplus
from .worksapces import workspace_repo
from aworld.checkpoint import create_checkpoint, CheckpointMetadata, Checkpoint, VersionUtils
from aworld.checkpoint.inmemory import InMemoryCheckpointRepository
from aworld.core.memory import AgentMemoryConfig
from aworld.memory.models import UserProfile, MemoryMessage, MemoryHumanMessage, MemoryAIMessage, MessageMetadata, \
    ConversationSummary
from aworld.models.llm import acall_llm_model

class ContextManager(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    checkpoint_repo: InMemoryCheckpointRepository = None

    def __init__(self, checkpoint_repo: Optional[InMemoryCheckpointRepository] = None):
        super().__init__()
        from aworld.memory.main import MemoryFactory
        self._memory = MemoryFactory.instance()
        self.checkpoint_repo = checkpoint_repo or WorkspaceCheckpointRepository(workspaces=workspace_repo)

    ###########################  Memory Backend ###########################

    async def get_user_similar_task(self, task_input: TaskInput) -> Optional[list[MemoryMessage]]:
        """
        get user similar history from memory
        if user has similar history, return the history
        if not, return None
        
        Args:
            task_input: current task input

        Returns:
            list[MemoryMessage]: user similar history

        """

        #1. get most similar session_id
        similar_conversation = self._memory.search(task_input.task_content, memory_type="conversation_summary", filters={
            "user_id": task_input.user_id,
            "agent_id": task_input.agent_id
        })
        if not similar_conversation or not len(similar_conversation) > 0 and not isinstance(similar_conversation[0], ConversationSummary):
            return []
        session_id = similar_conversation[0].session_id

        # 2. Get historical conversation by session_id
        session_messages = self._memory.get_all(filters={
            "user_id": task_input.user_id,
            "agent_id": task_input.agent_id,
            "session_id": session_id,
            "memory_type": ["init", "message"]
        })
        logger.info(f"retrival similar task {task_input.task_content} -> {session_messages}")
        return session_messages

    async def get_user_profiles(self, task_input: TaskInput) -> Optional[list[UserProfile]]:
        # get cur user profile
        # rela_user_profiles = await self._memory.retrival_user_profile(task_input.user_id, task_input.origin_user_input)
        # ALL IN
        rela_user_profiles = self._memory.get_all(filters={
            "memory_type":"user_profile",
            "user_id":task_input.user_id
        })
        profile_items = []
        if rela_user_profiles:
            profile_items = [item for item in rela_user_profiles if item is not None]
        return profile_items

    async def get_task_histories(self, task_input: TaskInput) ->  Optional[list[MemoryMessage]]:
        if not task_input.agent_id:
            return []
        # get cur session history
        return self._memory.get_last_n(10, filters={
            "user_id": task_input.user_id,
            "session_id": task_input.session_id,
            "agent_id": task_input.agent_id
        })

    async def _get_memory_config(self, agent_id):
        return AgentMemoryConfig()

    async def save_context(self, context: "ApplicationContext", **kwargs) -> None:
        """
        Save context to memory and checkpoint concurrently.emo

        Args:
            context: ApplicationContext
            kwargs: additional keyword arguments

        Returns:
            None
        """
        # 1. Save conversations to memory
        save_memory_task = self._save_conversations_to_memory(context)

        # 2. Save checkpoint
        save_checkpoint_task = self._save_context_checkpoint_async(context, **kwargs)

        # 3. Execute all three tasks concurrently
        await asyncio.gather(
            save_memory_task,
            save_checkpoint_task
        )
        
        logger.info(f"[ContextManager] save context finished, session {context.session_id}, task {context.task_id}")

    async def _save_conversations_to_memory(self, context: "ApplicationContext") -> None:
        """Save user input and task result to memory."""

        metadata = self._build_memory_message_metadata(context.task_input_object)

        await self._memory.add(MemoryHumanMessage(content=context.task_input_object.task_content, metadata=metadata))
        # TODO: Optimize - only save core content
        await self._memory.add(MemoryAIMessage(content=await TaskFormatter.format_task_history(context) if not context.task_output else context.task_output, metadata=metadata))
        logger.info(f"[ContextManager] add task result to memory, session {context.session_id}, task {context.task_id}")

        # Create async task for conversation summary with logging and callback
        task_id = f"summary_{context.session_id}_{context.task_id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        async def summary_task_with_callback():
            start_time = datetime.datetime.now()
            try:
                logger.info(f"[ContextManager] [{task_id}] Starting conversation summary task, session {context.session_id}, task {context.task_id}")
                await self._add_conversations_summary(context, copy.deepcopy(metadata))
                
                end_time = datetime.datetime.now()
                duration = (end_time - start_time).total_seconds()
                logger.info(f"[ContextManager] [{task_id}] Conversation summary task completed successfully, session {context.session_id}, task {context.task_id}, duration: {duration:.2f}s")
                
                # Callback after successful completion
                await self._on_summary_completed(context, metadata, task_id, duration)
                
            except Exception as err:
                end_time = datetime.datetime.now()
                duration = (end_time - start_time).total_seconds()
                logger.error(f"[ContextManager] [{task_id}] Conversation summary task failed, session {context.session_id}, task {context.task_id}, duration: {duration:.2f}s, error: {err}")
                # Callback after failure
                await self._on_summary_failed(context, metadata, err, task_id, duration)

        # Start the async task
        asyncio.create_task(summary_task_with_callback())
        logger.info(f"[ContextManager] Created async summary task: {task_id}, session {context.session_id}, task {context.task_id}")

    async def _add_conversations_summary(self, context: "ApplicationContext", summary_metadata: MessageMetadata) -> None:
        """Add conversations summary to memory."""
        summary_metadata.task_id = None
        messages = self._memory.get_all(filters={
            "agent_id": summary_metadata.agent_id,
            "user_id": summary_metadata.user_id,
            "session_id": summary_metadata.session_id,
            "memory_type": ["message", "init"]
        })

        conversation_content = ""   
        for message in messages:
            conversation_content += f"{message.to_openai_message()}\n"

        prompt = AMNI_CONTEXT_PROMPT["SUMMARY_CONVERSATION"].format(conversation_content=conversation_content)
        try:
            response = await acall_llm_model(self._memory._llm_instance, [{"role":"user", "content": prompt}])
            summary = jsonplus.parse_json_to_model(response.content, Summary)
            conversation_summary = ConversationSummary(context.user_id, context.session_id, summary.summary_content, metadata=summary_metadata)
            await self._memory.add(conversation_summary)
            logger.info(f"[ContextManager] add conversations summary to memory, session {context.session_id}, task {context.task_id}, summary {summary.summary_content[:100]}")
        except Exception as err:
            logger.warning(f"[ContextManager] add conversations summary to memory failed, session {context.session_id}, task {context.task_id}, error {err}, traceback {traceback.format_exc()}")

    async def _on_summary_completed(self, context: "ApplicationContext", metadata: MessageMetadata, task_id: str, duration: float) -> None:
        """Callback method called after conversation summary is completed successfully."""
        try:
            logger.info(f"[ContextManager] [{task_id}] Summary completion callback triggered, session {context.session_id}, task {context.task_id}")
            
            # Add any post-completion logic here
            # For example: update status, trigger notifications, etc.
            
            # Log completion metrics with performance data
            logger.info(f"[ContextManager] [{task_id}] Summary completion metrics - session: {context.session_id}, task: {context.task_id}, duration: {duration:.2f}s, timestamp: {datetime.datetime.now()}")
            
            # You can add more post-completion logic here:
            # - Update task status in database
            # - Send completion notification
            # - Update performance metrics
            # - Trigger next workflow step
            
        except Exception as callback_err:
            logger.error(f"[ContextManager] [{task_id}] Summary completion callback failed, session {context.session_id}, task {context.task_id}, error: {callback_err}")

    async def _on_summary_failed(self, context: "ApplicationContext", metadata: MessageMetadata, error: Exception, task_id: str, duration: float) -> None:
        """Callback method called after conversation summary fails."""
        try:
            logger.error(f"[ContextManager] [{task_id}] Summary failure callback triggered, session {context.session_id}, task {context.task_id}, error: {error}")
            
            # Add any failure handling logic here
            # For example: retry logic, error reporting, etc.
            
            # Log failure metrics with performance data
            logger.error(f"[ContextManager] [{task_id}] Summary failure metrics - session: {context.session_id}, task: {context.task_id}, duration: {duration:.2f}s, error: {error}, timestamp: {datetime.datetime.now()}")
            
            # You can add more failure handling logic here:
            # - Retry the summary task
            # - Send error notification
            # - Update error metrics
            # - Fallback to alternative processing
            
        except Exception as callback_err:
            logger.error(f"[ContextManager] [{task_id}] Summary failure callback failed, session {context.session_id}, task {context.task_id}, callback error: {callback_err}")

    async def _save_context_checkpoint_async(self, context: "ApplicationContext", **kwargs) -> None:
        """Save context checkpoint asynchronously."""

        await self.save_context_checkpoint(context, **kwargs)
        logger.info(f"[ContextManager] save checkpoint finished, session {context.session_id}, task {context.task_id}")

    
    def _build_memory_message_metadata(self, input: TaskInput) -> MessageMetadata:
        return MessageMetadata(
            user_id=input.user_id,
            session_id=input.session_id,
            task_id=input.task_id,
            agent_id=input.agent_id,
            agent_name=input.model,
            origin_user_input=input.origin_user_input
        )



    ###########################  Checkpoint Backend ###########################

    async def save_context_checkpoint(self, context: "ApplicationContext", **kwargs) -> Checkpoint:
        logger.info(f"[ContextManager] Saving checkpoint for session {context.session_id}, task {context.task_id}")
        session_id = context.session_id
        task_id = context.task_id

        # Use new Context functionality to create complete session state snapshot
        values = context.to_dict()

        # Add additional parameters
        values.update(kwargs)

        metadata = CheckpointMetadata(
            session_id=session_id,
            task_id=task_id
        )
        # Find last version checkpoint by session_id
        last_checkpoint = await self.checkpoint_repo.aget_by_session(session_id)

        checkpoint = create_checkpoint(values=values, metadata=metadata, version=VersionUtils.get_next_version(last_checkpoint.version) if last_checkpoint else 1)
        await self.checkpoint_repo.aput(checkpoint)

        logger.info(f"[ContextManager] Complete checkpoint saved for session {metadata.session_id}, task {metadata.task_id}")

        return checkpoint

    async def build_context_from_checkpoint(self, session_id: str) -> "ApplicationContext":
        # Query checkpoint
        checkpoint = await self.aget_checkpoint(session_id)
        if not checkpoint:
            logger.warning(f"[ContextManager] No checkpoint found for session {session_id}")
            return None

        logger.info(f"[ContextManager] Found checkpoint for session {session_id}, task {checkpoint.metadata.task_id}")

        # Restore Context from checkpoint
        from ... import ApplicationContext
        context = ApplicationContext.from_dict(checkpoint.values)

        workspace = await workspace_repo.get_session_workspace(session_id=context.session_id)
        context.workspace = workspace

        logger.info(f"[ContextManager] Successfully reloaded context for session {session_id} {context}")
        return context

    async def aget_checkpoint(self, session_id: str) -> Optional[Checkpoint]:
        return await self.checkpoint_repo.aget_by_session(session_id)

    def delete_checkpoint(self, session_id: str) -> None:
        self.checkpoint_repo.delete_by_session(session_id)
        logger.info(f"[ContextManager] Deleted checkpoint for session {session_id}")

    def list_checkpoints(self, **params) -> list:
        return self.checkpoint_repo.list(params)

    async def aget_checkpoint_artifact(self, context: "ApplicationContext", session_id: str) -> Optional[CheckpointArtifact]:
        checkpoint = await self.aget_checkpoint(session_id)
        if checkpoint and checkpoint.metadata and checkpoint.metadata.artifact_id:
            workspace = await workspace_repo.get_session_workspace(session_id)
            return workspace.get_artifact(checkpoint.metadata.artifact_id)
        return None