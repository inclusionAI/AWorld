"""
Task builder implementations.
"""
from typing import Dict, Any
from .config import AgentConfig


class SimpleTaskBuilder:
    """
    Simple task builder that constructs prompts from records.

    Uses the query column directly as the task prompt.

    Example:
        >>> builder = SimpleTaskBuilder(agent_config)
        >>> task = builder.build_task({"row_id": "0", "query": "create a ppt"})
        >>> print(task["prompt"])
        create a ppt
    """

    def __init__(self, agent_config: AgentConfig, query_column: str = "query"):
        """
        Initialize simple task builder.

        Args:
            agent_config: Agent configuration
            query_column: Column name containing the query/prompt
        """
        self.agent_config = agent_config
        self.query_column = query_column

    def build_task(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build a task specification from a record.

        Args:
            record: Record dictionary with row data

        Returns:
            Task specification dict with:
            - record_id: Original record ID
            - prompt: Task prompt extracted from query_column
            - agent_name: Agent name from config

        Example:
            >>> task = builder.build_task({"row_id": "0", "query": "create a ppt"})
            >>> print(task["prompt"])
            create a ppt
        """
        if self.query_column not in record:
            raise ValueError(f"❌ Query column '{self.query_column}' not found in record: {record}")

        return {
            "record_id": record.get("row_id", "unknown"),
            "prompt": record[self.query_column],
            "agent_name": self.agent_config.name,
            "original_record": record  # Keep original for output
        }
