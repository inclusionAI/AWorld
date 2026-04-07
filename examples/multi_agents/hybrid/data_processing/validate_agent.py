# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""ValidateAgent: Validates transformed email data and provides feedback."""
from typing import List, Dict, Any

from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation
from aworld.logs.util import logger


class ValidateAgent(Agent):
    """Agent responsible for validating transformed email data.

    In Hybrid mode, this agent:
    - Responds to FilterAgent's filtering strategy questions
    - Provides feedback to TransformAgent about transformation quality
    - Broadcasts final validation status to all peers
    """

    def __init__(self, **kwargs):
        super().__init__(
            name="ValidateAgent",
            desc="Validates transformed email data and provides quality feedback",
            **kwargs
        )
        self.validation_results = []
        self.issues_found = []

    async def async_policy(
        self,
        observation: Observation,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """Validate transformed emails and collaborate with peers if in Hybrid mode.

        Args:
            observation: Input data from TransformAgent

        Returns:
            List of actions to execute
        """
        logger.info(f"[{self.name()}] Starting email validation")

        # Parse input (assuming TransformAgent's output)
        input_data = observation.content
        if isinstance(input_data, dict):
            email_list = input_data.get("transformed_emails", [])
        elif isinstance(input_data, list):
            email_list = input_data
        else:
            email_list = []

        logger.info(f"[{self.name()}] Validating {len(email_list)} transformed emails")

        # Validate each transformed email
        valid_count = 0
        for item in email_list:
            if isinstance(item, dict):
                standardized = item.get("standardized", "")
                original = item.get("original", "")
                domain = item.get("domain", "")

                # Validation rules
                issues = []

                # Check format consistency
                if standardized != standardized.lower():
                    issues.append("not_lowercase")

                # Check domain validity (simple check)
                if not domain or '.' not in domain:
                    issues.append("invalid_domain")

                # Check transformation correctness
                if '@' in original and '@' not in standardized:
                    issues.append("transformation_error")

                if issues:
                    self.issues_found.append({
                        "email": standardized,
                        "issues": issues
                    })
                    logger.warning(
                        f"[{self.name()}] Issues found in {standardized}: {issues}"
                    )
                else:
                    valid_count += 1
                    self.validation_results.append({
                        "email": standardized,
                        "status": "valid"
                    })

        # Calculate validation metrics
        total_count = len(email_list)
        pass_rate = valid_count / total_count if total_count > 0 else 0

        logger.info(
            f"[{self.name()}] Validation complete: "
            f"{valid_count}/{total_count} passed ({pass_rate:.1%})"
        )

        # === Peer Collaboration (only in Hybrid mode) ===
        if self._is_peer_enabled:
            logger.info(f"[{self.name()}] Peer mode enabled, providing feedback")

            # If issues found, share feedback with TransformAgent
            if self.issues_found:
                try:
                    await self.share_with_peer(
                        peer_name="TransformAgent",
                        information={
                            "issues": self.issues_found,
                            "issue_count": len(self.issues_found),
                            "suggestion": "Check transformation rules for consistency"
                        },
                        info_type="quality_feedback"
                    )
                    logger.info(
                        f"[{self.name()}] Sent quality feedback to TransformAgent"
                    )
                except Exception as e:
                    logger.warning(f"[{self.name()}] Failed to share feedback: {e}")

            # Broadcast final validation status to all peers
            try:
                broadcast_count = await self.broadcast_to_all_peers(
                    information={
                        "status": "validation_complete",
                        "total_count": total_count,
                        "valid_count": valid_count,
                        "pass_rate": pass_rate,
                        "issues_found": len(self.issues_found)
                    },
                    info_type="validation_status"
                )
                logger.info(
                    f"[{self.name()}] Broadcast validation status to {broadcast_count} peers"
                )
            except Exception as e:
                logger.warning(f"[{self.name()}] Failed to broadcast status: {e}")

        # Prepare result
        result = {
            "agent": self.name(),
            "validation_results": self.validation_results,
            "issues_found": self.issues_found,
            "total_count": total_count,
            "valid_count": valid_count,
            "pass_rate": pass_rate,
            "quality_score": pass_rate * 100  # Simple quality score
        }

        logger.info(f"[{self.name()}] Final quality score: {result['quality_score']:.1f}")

        return [self.to_action_model(result)]
