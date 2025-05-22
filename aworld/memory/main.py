from typing import Optional
from aworld.core.memory import MemoryBase, MemoryItem, MemoryStore, MemoryConfig


class InMemoryMemoryStore(MemoryStore):
    def __init__(self):
        self.memory_items = []

    def add(self, memory_item: MemoryItem):
        self.memory_items.append(memory_item)

    def get(self, memory_id) -> Optional[MemoryItem]:
        return next((item for item in self.memory_items if item.id == memory_id), None)

    def get_first(self, filters: dict = None) -> Optional[MemoryItem]:
        """
        Get the first memory item.
        """
        filtered_items = self.get_all(filters)
        if len(filtered_items) == 0:
            return None
        return filtered_items[0]

    def total_rounds(self, filters: dict = None) -> int:
        """
        Get the total number of rounds.
        """
        return len(self.get_all(filters))

    def get_all(self, filters: dict = None) -> list[MemoryItem]:
        """
        Filter memory items based on filters.
        """
        filtered_items = [item for item in self.memory_items if self._filter_memory_item(item, filters)]
        return filtered_items
    
    def _filter_memory_item(self, memory_item: MemoryItem, filters: dict = None) -> bool:
        if memory_item.deleted:
            return False
        if filters is None:
            return True
        if filters.get('user_id') is not None:
            if memory_item.metadata.get('user_id') is None:
                return False
            if memory_item.metadata.get('user_id') != filters['user_id']:
                return False
        if filters.get('agent_id') is not None:
            if memory_item.metadata.get('agent_id') is None:
                return False
            if memory_item.metadata.get('agent_id') != filters['agent_id']:
                return False
        if filters.get('task_id') is not None:
            if memory_item.metadata.get('task_id') is None:
                return False
            if memory_item.metadata.get('task_id') != filters['task_id']:
                return False
        if filters.get('session_id') is not None:
            if memory_item.metadata.get('session_id') is None:
                return False
            if memory_item.metadata.get('session_id') != filters['session_id']:
                return False
        if filters.get('message_type') is not None:
            if memory_item.message_type is None:
                return False
            if memory_item.message_type != filters['message_type']:
                return False
        return True

    def get_last_n(self, last_rounds, filters: dict = None) -> list[MemoryItem]:
        return self.memory_items[-last_rounds:]  # Get the last n items

    def update(self, memory_item: MemoryItem):
        for index, item in enumerate(self.memory_items):
            if item.id == memory_item.id:
                self.memory_items[index] = memory_item  # Update the item in the list
                break

    def delete(self, memory_id):
        exists = self.get(memory_id)
        if exists:
            exists.deleted = True

    def history(self, memory_id) -> list[MemoryItem] | None:
        exists = self.get(memory_id)
        if exists:
            return exists.histories
        return None


class MemoryFactory:

    @classmethod
    def from_config(cls, config: MemoryConfig) -> "MemoryBase":
        """
        Initialize a Memory instance from a configuration dictionary.

        Args:
            config (dict): Configuration dictionary.

        Returns:
            Memory: Memory instance.
        """
        if config.provider == "inmemory":
            return Memory(
                memory_store=InMemoryMemoryStore(),
                enable_summary=config.enable_summary,
                summary_rounds=config.summary_rounds
            )
        elif config.provider == "mem0":
            from aworld.memory.mem0.mem0_memory import Mem0Memory
            return Mem0Memory(
                memory_store=InMemoryMemoryStore(),
                config=config
            )
        else:
            raise ValueError(f"Invalid memory store type: {config.get('memory_store')}")


class Memory(MemoryBase):

    def __init__(self, memory_store: MemoryStore, enable_summary: bool = True, **kwargs):
        self.memory_store = memory_store
        self.summary = {}
        self.summary_rounds = kwargs.get("summary_rounds", 10)
        self.enable_summary = enable_summary

    def add(self, memory_item: MemoryItem, filters: dict = None):
        self.memory_store.add(memory_item)

        # Check if we need to create or update summary
        if self.enable_summary:
            total_rounds = len(self.memory_store.get_all())
            if total_rounds > self.summary_rounds:
                self._create_or_update_summary(total_rounds)

    def _create_or_update_summary(self, total_rounds: int):
        """
        Create or update summary based on current total rounds.

        Args:
            total_rounds (int): Total number of rounds.
        """
        summary_index = int(total_rounds / self.summary_rounds)
        start = (summary_index - 1) * self.summary_rounds
        end = total_rounds - self.summary_rounds

        # Ensure we have valid start and end indices
        start = max(0, start)
        end = max(start, end)

        # Get the memory items to summarize
        items_to_summarize = self.memory_store.get_all()[start:end + 1]
        print(f"{total_rounds}start: {start}, end: {end},")

        # Create summary content
        summary_content = self._summarize_items(items_to_summarize, summary_index)

        # Create the range key
        range_key = f"{start}_{end}"

        # Check if summary for this range already exists
        if range_key in self.summary:
            # Update existing summary
            self.summary[range_key].content = summary_content
            self.summary[range_key].updated_at = None  # This will update the timestamp
        else:
            # Create new summary
            summary_item = MemoryItem(
                content=summary_content,
                metadata={
                    "summary_index": summary_index,
                    "start_round": start,
                    "end_round": end,
                    "role": "system"
                },
                tags=["summary"]
            )
            self.summary[range_key] = summary_item

    def _summarize_items(self, items: list[MemoryItem], summary_index: int) -> str:
        """
        Summarize a list of memory items.

        Args:
            items (list[MemoryItem]): List of memory items to summarize.
            summary_index (int): Summary index.

        Returns:
            str: Summary content.
        """
        # This is a placeholder. In a real implementation, you might use an LLM or other method
        # to create a meaningful summary of the content
        contents = [item.content for item in items]
        return f"Summary {summary_index}: Summarized content from rounds {items[0].metadata.get('round', 'unknown')} to {items[-1].metadata.get('round', 'unknown')}"

    def update(self, memory_item: MemoryItem):
        self.memory_store.update(memory_item)

    def delete(self, memory_id):
        self.memory_store.delete(memory_id)

    def get(self, memory_id) -> Optional[MemoryItem]:
        return self.memory_store.get(memory_id)

    def get_all(self, filters: dict = None) -> list[MemoryItem]:
        return self.memory_store.get_all()

    def get_last_n(self, last_rounds, add_first_message=True, filters: dict = None) -> list[MemoryItem]:
        """
        Get last n memories.

        Args:
            last_rounds (int): Number of memories to retrieve.
            add_first_message (bool):

        Returns:
            list[MemoryItem]: List of latest memories.
        """
        memory_items = self.memory_store.get_last_n(last_rounds)
        while len(memory_items) > 0 and memory_items[0].metadata and "tool_call_id" in memory_items[0].metadata and \
                memory_items[0].metadata["tool_call_id"]:
            last_rounds = last_rounds + 1
            memory_items = self.memory_store.get_last_n(last_rounds)

        # If summary is disabled or no summaries exist, return just the last_n_items
        if not self.enable_summary or not self.summary:
            return memory_items

        # Calculate the range for relevant summaries
        all_items = self.memory_store.get_all()
        total_items = len(all_items)
        end_index = total_items - last_rounds

        # Get complete summaries
        result = []
        complete_summary_count = end_index // self.summary_rounds

        # Get complete summaries
        for i in range(complete_summary_count):
            range_key = f"{i * self.summary_rounds}_{(i + 1) * self.summary_rounds - 1}"
            if range_key in self.summary:
                result.append(self.summary[range_key])

        # Get the last incomplete summary if exists
        remaining_items = end_index % self.summary_rounds
        if remaining_items > 0:
            start = complete_summary_count * self.summary_rounds
            range_key = f"{start}_{end_index - 1}"
            if range_key in self.summary:
                result.append(self.summary[range_key])

        # Add the last n items
        result.extend(memory_items)

        # Add first user input
        if add_first_message and last_rounds < self.memory_store.total_rounds():
            memory_items.insert(0, self.memory_store.get_first())

        return result