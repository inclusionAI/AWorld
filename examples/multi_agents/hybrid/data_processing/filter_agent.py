# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""FilterAgent: Filters invalid email addresses from input data."""
import re
from typing import List, Dict, Any

from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation
from aworld.logs.util import logger


class FilterAgent(Agent):
    """Agent responsible for filtering invalid email addresses.

    In Hybrid mode, this agent collaborates with ValidateAgent to confirm
    filtering rules and with TransformAgent to share filtered data format.
    """

    def __init__(self, **kwargs):
        super().__init__(
            name="FilterAgent",
            desc="Filters invalid email addresses from input data",
            **kwargs
        )
        self.filtered_data = []
        self.invalid_data = []

    async def async_policy(
        self,
        observation: Observation,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """Filter email addresses and collaborate with peers if in Hybrid mode.

        Args:
            observation: Input data containing email list

        Returns:
            List of actions to execute
        """
        logger.info(f"[{self.name()}] Starting email filtering")

        # Parse input data
        input_data = observation.content
        if isinstance(input_data, str):
            # Parse from string: '["email1", "email2", ...]'
            import json
            try:
                email_list = json.loads(input_data)
            except:
                email_list = input_data.split(',')
        elif isinstance(input_data, list):
            email_list = input_data
        else:
            email_list = [str(input_data)]

        logger.info(f"[{self.name()}] Processing {len(email_list)} emails")

        # Basic email pattern
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'

        # Filter emails
        for email in email_list:
            email = str(email).strip()
            if re.match(email_pattern, email):
                self.filtered_data.append(email)
            else:
                self.invalid_data.append(email)
                logger.info(f"[{self.name()}] Filtered out invalid: {email}")

        # === Peer Collaboration (only in Hybrid mode) ===
        if self._is_peer_enabled:
            logger.info(f"[{self.name()}] Peer mode enabled, sharing results with peers")

            # Share filtered data format with TransformAgent (non-blocking)
            try:
                await self.share_with_peer(
                    peer_name="TransformAgent",
                    information={
                        "stage": "filter_complete",
                        "format": "standard_email",
                        "sample": self.filtered_data[0] if self.filtered_data else None,
                        "valid_count": len(self.filtered_data),
                        "invalid_count": len(self.invalid_data)
                    },
                    info_type="data_format"
                )
                logger.info(f"[{self.name()}] Shared filtering results with TransformAgent")
            except Exception as e:
                logger.warning(f"[{self.name()}] Failed to share with TransformAgent: {e}")

            # Broadcast filtering summary to all peers (non-blocking)
            try:
                await self.broadcast_to_all_peers(
                    information={
                        "stage": "filter",
                        "status": "complete",
                        "total_input": len(email_list),
                        "valid_output": len(self.filtered_data),
                        "filter_rate": len(self.filtered_data) / len(email_list) if email_list else 0
                    },
                    info_type="status"
                )
                logger.info(f"[{self.name()}] Broadcast filtering status to all peers")
            except Exception as e:
                logger.warning(f"[{self.name()}] Failed to broadcast status: {e}")

        # Prepare result
        result = {
            "agent": self.name(),
            "valid_emails": self.filtered_data,
            "invalid_emails": self.invalid_data,
            "valid_count": len(self.filtered_data),
            "invalid_count": len(self.invalid_data),
            "filter_rate": len(self.filtered_data) / len(email_list) if email_list else 0
        }

        logger.info(
            f"[{self.name()}] Filtering complete: "
            f"{len(self.filtered_data)} valid, {len(self.invalid_data)} invalid"
        )

        # Return result as observation
        return [self.to_action_model(result)]
