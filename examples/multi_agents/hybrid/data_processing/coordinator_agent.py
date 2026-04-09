# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""DataCoordinator: Orchestrates the data processing pipeline."""
from typing import List, Dict, Any

from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation
from aworld.logs.util import logger


class DataCoordinator(Agent):
    """Root agent that coordinates the data processing pipeline.

    Responsibilities:
    - Receive input data
    - Assign subtasks to executor agents (FilterAgent, TransformAgent, ValidateAgent)
    - Collect and synthesize final results
    """

    def __init__(self, **kwargs):
        super().__init__(
            name="DataCoordinator",
            desc="Coordinates data processing pipeline across filter, transform, and validate stages",
            **kwargs
        )

    async def async_policy(
        self,
        observation: Observation,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """Coordinate data processing across executor agents.

        Args:
            observation: Input data (list of emails to process)

        Returns:
            List of actions to execute
        """
        logger.info(f"[{self.name()}] Starting data processing coordination")

        # Parse input
        input_data = observation.content
        logger.info(f"[{self.name()}] Received input: {input_data}")

        # In a real implementation with TeamSwarm/HybridSwarm,
        # this coordinator would delegate to executors via handoffs.
        # For this demonstration, we return a task decomposition plan.

        result = {
            "agent": self.name(),
            "status": "coordinating",
            "input_data": input_data,
            "pipeline": [
                {"stage": "filter", "agent": "FilterAgent"},
                {"stage": "transform", "agent": "TransformAgent"},
                {"stage": "validate", "agent": "ValidateAgent"}
            ],
            "message": "Data processing pipeline initiated"
        }

        logger.info(f"[{self.name()}] Coordination plan created")

        return [self.to_action_model(result)]
