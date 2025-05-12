from typing import Optional
from aworld.core.memory import MemoryBase, MemoryItem, MemoryStore


class InMemoryMemoryStore(MemoryStore):
    def __init__(self):
        self.memory_items = []  # Initialize as a list

    def add(self, memory_item: MemoryItem):
        self.memory_items.append(memory_item)  # Append memory item to the list

    def get(self, memory_id) -> Optional[MemoryItem]:
        return next((item for item in self.memory_items if item.id == memory_id), None)  # Find item by ID

    def get_first(self) -> Optional[MemoryItem]:
        if len(self.memory_items) == 0:
            return None
        return self.memory_items[0]

    def total_rounds(self) -> int:
        return len(self.memory_items)

    def get_all(self) -> list[MemoryItem]:
        return self.memory_items  # Return all items directly

    def get_last_n(self, last_rounds) -> list[MemoryItem]:
        return self.memory_items[-last_rounds:]  # Get the last n items

    def update(self, memory_item: MemoryItem):
        for index, item in enumerate(self.memory_items):
            if item.id == memory_item.id:
                self.memory_items[index] = memory_item  # Update the item in the list
                break

    def delete(self, memory_id):
        self.memory_items = [item for item in self.memory_items if item.id != memory_id]  # Remove item by ID

    def retrieve(self, query, filters: dict) -> list[MemoryItem]:
        return [memory_item for memory_item in self.memory_items if
                memory_item.content.lower().find(query.lower()) != -1]

    def history(self, memory_id) -> list[MemoryItem]:
        return [memory_item for memory_item in self.memory_items if memory_item.id == memory_id]


class Memory(MemoryBase):

    def __init__(self, memory_store: MemoryStore, enable_summary: bool = True, **kwargs):
        self.memory_store = memory_store
        self.summary = {}
        self.summary_rounds = kwargs.get("summary_rounds", 10)
        self.enable_summary = enable_summary

    @classmethod
    def from_config(cls, config: dict) -> "Memory":
        """
        Initialize a Memory instance from a configuration dictionary.

        Args:
            config (dict): Configuration dictionary.

        Returns:
            Memory: Memory instance.
        """
        if config.get("memory_store") == "inmemory":
            return cls(
                memory_store=InMemoryMemoryStore(),
                enable_summary=config.get("enable_summary", False),
                summary_rounds=config.get("summary_rounds", 5)
            )
        else:
            raise ValueError(f"Invalid memory store type: {config.get('memory_store')}")

    def add(self, memory_item: MemoryItem):
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

    def get_all(self) -> list[MemoryItem]:
        return self.memory_store.get_all()

    def get_last_n(self, last_rounds, add_first_message=True) -> list[MemoryItem]:
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

    def retrieve(self, query, filters: dict) -> list[MemoryItem]:
        return self.memory_store.retrieve(query, filters)

    def history(self, memory_id) -> list[MemoryItem]:
        return self.memory_store.history(memory_id)

    def get_summary(self) -> list[MemoryItem]:
        """
        Get all summaries.

        Returns:
            list[MemoryItem]: List of summary items.
        """
        return list(self.summary.values())
