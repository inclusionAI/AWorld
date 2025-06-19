import json
import os
import traceback
from typing import Optional

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from aworld.core.memory import MemoryStore, MemoryConfig, MemoryItem
from aworld.logs.util import logger
from aworld.memory.main import Memory


class Mem0Memory(Memory):

    def __init__(self, memory_store: MemoryStore, config: MemoryConfig | None = None, **kwargs):
        super().__init__(memory_store, config, **kwargs)
        self.config = config

        self.config.llm_instance = ChatOpenAI(
            model=os.getenv("MEM_LLM_MODEL_NAME") if os.getenv("MEM_LLM_MODEL_NAME") else os.getenv(
                'LLM_MODEL_NAME'),
            api_key=os.getenv("MEM_LLM_API_KEY") if os.getenv("MEM_LLM_API_KEY") else os.getenv('LLM_API_KEY'),
            base_url=os.getenv("MEM_LLM_BASE_URL") if os.getenv("MEM_LLM_BASE_URL") else os.getenv('LLM_BASE_URL'),
            temperature=os.getenv("MEM_LLM_TEMPERATURE") if os.getenv("MEM_LLM_TEMPERATURE") else 1.0,
            streaming=False
        )

        # Check for required packages
        try:
            # also disable mem0's telemetry when ANONYMIZED_TELEMETRY=False
            if os.getenv('ANONYMIZED_TELEMETRY', 'true').lower()[0] in 'fn0':
                os.environ['MEM0_TELEMETRY'] = 'False'
            from mem0 import Memory as Mem0Memory
        except ImportError:
            raise ImportError('mem0 is required when enable_memory=True. Please install it with `pip install mem0`.')

        if self.config.embedder_provider == 'huggingface':
            try:
                # check that required package is installed if huggingface is used
                from sentence_transformers import SentenceTransformer  # noqa: F401
            except ImportError:
                raise ImportError(
                    'sentence_transformers is required when enable_memory=True and embedder_provider="huggingface". Please install it with `pip install sentence-transformers`.'
                )

        # Initialize Mem0 with the configuration
        config_dict = self.config.full_config_dict
        self.mem0 = Mem0Memory.from_config(config_dict=self.config.full_config_dict)
        self.memory_store = memory_store

    def add(self, memory_item: MemoryItem, filters: dict = None):
        # generate summary memory if needed
        message_filters = {
            "memory_type": "message"
        }
        if filters:
            message_filters = {
                "memory_type": "message",
                "agent_id": memory_item.metadata.get("agent_id"),
                "task_id": memory_item.metadata.get("task_id"),
                "user_id": memory_item.metadata.get("user_id"),
                "session_id": memory_item.metadata.get("session_id"),
            }

        if self._need_summary(memory_item, message_filters):
            self.create_summary_memory(
                agent_id=memory_item.metadata.get("agent_id"),
                task_id=memory_item.metadata.get("task_id"),
                user_id=memory_item.metadata.get("user_id"),
                session_id=memory_item.metadata.get("session_id"),
                filters=message_filters
            )
        else:
            self.memory_store.add(memory_item)

    def _need_summary(self, memory_item, message_filters):
        """
        Check if a summary is needed based on the current step.
        1. If the number of messages is greater than the summary rounds.
        2. If the message is a message and the content is greater than the summary single context length.
        """
        return self.memory_store.total_rounds(message_filters) > self.config.summary_rounds or (
                memory_item.memory_type == 'message' and len(
            memory_item.content) >= self.config.summary_single_context_length)

    def create_summary_memory(self, agent_id, task_id, user_id, session_id, filters: dict) -> None:
        """
        Create a summary memory if needed based on the current step.
        """
        logger.info(f'Creating summary memory, {filters}')

        # Get all messages
        all_messages = self.memory_store.get_all(filters=filters)

        # Separate messages into those to keep as-is and those to process for memory
        summary_messages = []
        messages_to_process = []

        for msg in all_messages:
            if isinstance(msg, MemoryItem) and msg.memory_type in {'summary'}:
                # Keep system and memory messages as they are
                summary_messages.append(msg)
            elif msg.memory_type in {'init'}:
                messages_to_process.append(msg)
            else:
                if len(msg.content) > 0:
                    messages_to_process.append(msg)
        if messages_to_process[-1].metadata.get("tool_calls"):
            messages_to_process = messages_to_process[:-1]
        # Need at least 1 message to create a meaningful summary
        if len(messages_to_process) < 1:
            logger.info('Not enough non-memory messages to summarize')
            return
        # Create a procedural memory

        memory_content = self._create_summary_memory(messages_to_process)

        if not memory_content:
            logger.warning('Failed to create procedural memory')
            return

        # Add the summary message
        summary_message = MemoryItem(content=memory_content, memory_type='summary', metadata= {
            "role": "user",
            "agent_id": agent_id,
            "session_id": session_id,
            "task_id": task_id,
            "user_id": user_id,
        })
        summary_messages.append(summary_message)

        # Update the history
        [self.memory_store.delete(m.id) for m in messages_to_process]
        self.memory_store.add(summary_message)


        logger.info(f'Messages consolidated: {len(messages_to_process)} messages converted to procedural memory')

    def _create_summary_memory(self, messages: list[MemoryItem]) -> str | None:

        parsed_messages = [{'role': message.metadata['role'], 'content': message.content if not message.metadata.get('tool_calls') else message.content + "\n\n" + self.__format_tool_call(message.metadata.get('tool_calls')) } for message in messages] #TODO add tool_call from metadata['tool_calls']  such as [{"id": "fc-7b66b01a-f125-44d5-9f32-5e3723384d8e", "type": "function", "function": {"name": "mcp__amap-amap-sse__maps_geo", "arguments": "{\"address\": \"\u676d\u5dde\", \"city\": \"\u676d\u5dde\"}"}}] append to content
        try:
            results = self.mem0.add(
                messages=parsed_messages,
                agent_id=messages[-1].metadata.get('agent_id'),
                memory_type='procedural_memory'
            )
            if len(results.get('results', [])):
                logger.info(f'creating summary memory result: {results}')
                return results.get('results', [])[0].get('memory')
            return None
        except Exception as e:
            logger.error(f'Error creating summary memory: {e}')
            traceback.print_exc()
            return None
    def __format_tool_call(self, tool_calls):
        return json.dumps(tool_calls, default=lambda o: o.model_dump_json() if isinstance(o, BaseModel) else str(o))

    def update(self, memory_item: MemoryItem):
        self.memory_store.update(memory_item)

    def delete(self, memory_id):
        self.memory_store.delete(memory_id)



    def get(self, memory_id) -> Optional[MemoryItem]:
        # self.memory_store.get(memory_id)
        return self.memory_store.get(
            memory_id,
        )


    def get_all(self, filters: dict = None) -> list[MemoryItem]:
        return self.memory_store.get_all(
            filters=filters,
        )

    def get_last_n(self, last_rounds, add_first_message=True, filters: dict = None) -> list[MemoryItem]:
        """
        Get last n memories.

        Args:
            last_rounds (int): Number of memories to retrieve.
            add_first_message (bool):

        Returns:
            list[MemoryItem]: List of latest memories.
        """
        return self.memory_store.get_last_n(
            last_rounds=last_rounds,
            filters=filters,
        )
