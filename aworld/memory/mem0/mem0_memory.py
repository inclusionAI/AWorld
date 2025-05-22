import os
from dataclasses import Field
from typing import Optional

from langchain_core.messages import HumanMessage, convert_to_openai_messages
from langchain_openai import ChatOpenAI

from aworld.core.memory import MemoryStore, MemoryConfig, MemoryBase, MemoryItem
from aworld.logs.util import logger


class Mem0Memory(MemoryBase):

    def __init__(
            self,
            memory_store: MemoryStore,
            config: MemoryConfig | None = None,
    ):
        self.config = config # re-validate user-provided config

        self.config.llm_instance = ChatOpenAI(
            model=os.getenv("MEM0_LLM_MODEL_NAME"),
            api_key=os.getenv("MEM0_LLM_API_KEY"),
            base_url=os.getenv("MEM0_LLM_BASE_URL"),
            temperature=1.0,
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
        self.mem0 = Mem0Memory.from_config(config_dict=self.config.full_config_dict)
        self.memory_store = memory_store

    def add(self, memory_item: MemoryItem, filters: dict = None):
        self.memory_store.add(memory_item)
        # generate summary memory if needed
        message_filters = {
            "message_type": "message",
            **filters
        }
        if self.memory_store.total_rounds(message_filters) > self.config.summary_rounds == 0:
            self.create_procedural_memory(filters)

    def create_summary_memory(self, filters: dict) -> None:
        """
        Create a summary memory if needed based on the current step.
        """
        logger.info(f'Creating summary memory at step {current_step}')

        # Get all messages
        all_messages = self.memory_store.get_all(filters=filters)

        # Separate messages into those to keep as-is and those to process for memory
        summary_messages = []
        messages_to_process = []

        for msg in all_messages:
            if isinstance(msg, MemoryItem) and msg.metadata.get('message_type') in {'init', 'memory'}:
                # Keep system and memory messages as they are
                summary_messages.append(msg)
            else:
                if len(msg.content) > 0:
                    messages_to_process.append(msg)

        # Need at least 2 messages to create a meaningful summary
        if len(messages_to_process) <= 1:
            logger.info('Not enough non-memory messages to summarize')
            return
        # Create a procedural memory
        memory_content = self._create([m for m in messages_to_process], current_step)

        if not memory_content:
            logger.warning('Failed to create procedural memory')
            return

        # Add the summary message
        summary_message = MemoryItem(content=memory_content, message_type='summary', metadata= {
            "role": "user",
            **filters
        })
        summary_messages.append(summary_message)

        # Update the history
        [self.memory_store.delete(m.id) for m in messages_to_process]
        [self.memory_store.add(m) for m in summary_messages]


        logger.info(f'Messages consolidated: {len(messages_to_process)} messages converted to procedural memory')

    def _create(self, messages: list[MemoryItem], current_step: int) -> str | None:
        parsed_messages = convert_to_openai_messages(messages)
        try:
            results = self.mem0.add(
                messages=parsed_messages,
                agent_id=self.config.agent_id,
                memory_type='procedural_memory',
                metadata={'step': current_step},
            )
            if len(results.get('results', [])):
                return results.get('results', [])[0].get('memory')
            return None
        except Exception as e:
            logger.error(f'Error creating procedural memory: {e}')
            return None

    def update(self, memory_item: MemoryItem):
        self.memory_store.update(memory_item)
        self.mem0.update(
            memory_item.id,
            messages=memory_item.content,
        )

    def delete(self, memory_id):
        self.memory_store.delete(memory_id)
        self.mem0.delete(
            memory_id,
        )

    def get(self, memory_id) -> Optional[MemoryItem]:
        # self.memory_store.get(memory_id)
        return self.mem0.get(
            memory_id,
        )


    def get_all(self, filters: dict = None) -> list[MemoryItem]:
        return self.mem0.get_all(
            user_id=filters.get('user_id'),
            agent_id=filters.get('agent_id'),
            run_id=filters.get('task_id'),
        )['results']

    def get_last_n(self, last_rounds, add_first_message=True, filters: dict = None) -> list[MemoryItem]:
        """
        Get last n memories.

        Args:
            last_rounds (int): Number of memories to retrieve.
            add_first_message (bool):

        Returns:
            list[MemoryItem]: List of latest memories.
        """
        return self.mem0.get_all(
            user_id=filters.get('user_id'),
            agent_id=filters.get('agent_id'),
            run_id=filters.get('task_id'),
            limit=last_rounds
        )['results']
