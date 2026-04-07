# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""TransformAgent: Transforms email addresses to standardized format."""
from typing import List, Dict, Any

from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation
from aworld.logs.util import logger


class TransformAgent(Agent):
    """Agent responsible for transforming emails to standardized format.

    In Hybrid mode, this agent collaborates with FilterAgent to understand
    input format and with ValidateAgent to adjust transformation based on feedback.
    """

    def __init__(self, **kwargs):
        super().__init__(
            name="TransformAgent",
            desc="Transforms email addresses to standardized format",
            **kwargs
        )
        self.transformed_data = []
        self.received_feedback = []

    async def async_policy(
        self,
        observation: Observation,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """Transform email addresses with peer collaboration if in Hybrid mode.

        Args:
            observation: Input data from FilterAgent

        Returns:
            List of actions to execute
        """
        logger.info(f"[{self.name()}] Starting email transformation")

        # Parse input (assuming FilterAgent's output)
        input_data = observation.content
        if isinstance(input_data, dict):
            email_list = input_data.get("valid_emails", [])
        elif isinstance(input_data, list):
            email_list = input_data
        else:
            email_list = []

        logger.info(f"[{self.name()}] Transforming {len(email_list)} emails")

        # === Peer Collaboration (only in Hybrid mode) ===
        # Note: In Hybrid mode, TransformAgent may receive format info from FilterAgent
        # via peer broadcast, but continues execution without blocking

        # Transform emails to standardized format
        # Standardization: lowercase + domain normalization
        for email in email_list:
            email_str = str(email).strip()

            # Split into local and domain
            if '@' in email_str:
                local, domain = email_str.split('@', 1)

                # Standardize: lowercase + normalize domain
                standardized = f"{local.lower()}@{domain.lower()}"

                # Add metadata
                self.transformed_data.append({
                    "original": email_str,
                    "standardized": standardized,
                    "domain": domain.lower()
                })
            else:
                logger.warning(f"[{self.name()}] Invalid email format: {email_str}")

        logger.info(
            f"[{self.name()}] Transformation complete: {len(self.transformed_data)} emails"
        )

        # === Peer Collaboration: Share transformation results (only in Hybrid mode) ===
        if self._is_peer_enabled:
            try:
                await self.broadcast_to_all_peers(
                    information={
                        "stage": "transform",
                        "status": "complete",
                        "transformed_count": len(self.transformed_data),
                        "domains": list(set(item["domain"] for item in self.transformed_data))
                    },
                    info_type="status"
                )
                logger.info(f"[{self.name()}] Broadcast transformation status to all peers")
            except Exception as e:
                logger.warning(f"[{self.name()}] Failed to broadcast status: {e}")

        # Prepare result
        result = {
            "agent": self.name(),
            "transformed_emails": self.transformed_data,
            "transform_count": len(self.transformed_data)
        }

        return [self.to_action_model(result)]
