from typing import Any, Dict

from ... import ApplicationContext
from .op_factory import memory_op
from .langextract_op import LangExtractOp
from ...prompt.prompts import USER_PROFILE_FEW_SHOTS, AMNI_CONTEXT_PROMPT
from aworld.memory.models import UserProfile


@memory_op("extract_user_profile")
class UserProfileLangExtractOp(LangExtractOp[UserProfile]):
    """
    Concrete implementation for user profile extraction using langextract
    """

    def __init__(self, prompt: str = AMNI_CONTEXT_PROMPT["USER_PROFILE_EXTRACTION_PROMPT"],
                 few_shots=USER_PROFILE_FEW_SHOTS, **kwargs):
        """
        Initialize UserProfileLangExtractOp

        Args:
            prompt: Prompt template for profile extraction
            **kwargs: Additional configuration options
        """

        super().__init__("extract_user_profile", prompt, extraction_classes=["user_profile"], few_shots=few_shots,
                         **kwargs)

    def _prepare_extraction_text(self, context: ApplicationContext, agent_id: str) -> str:
        """
        Prepare text for user profile extraction

        Args:
            context: Application context
            agent_id: Agent identifier

        Returns:
            Formatted text for extraction
        """
        return (f"\n\nExisted Agent Experience: \n{context.get_user_profiles()}"
                f"\n\nConversation:\n{context.get_history_desc()}")

    def _build_memory_item(self, extract_data: Dict[str, Any], context: ApplicationContext, agent_id: str):

        if extract_data:
            profile_key = extract_data.get("key")
            profile_value = extract_data.get("value")
            if profile_key and profile_value is not None:
                return UserProfile(
                    user_id=context.user_id,
                    key=profile_key,
                    value=profile_value,
                    metadata={
                        "agent_name": context.task_input_object.model if agent_id == "default" else agent_id,
                        "agent_id": context.task_input_object.model if agent_id == "default" else agent_id,
                        "task_id": context.task_id,
                        "session_id": context.session_id,
                    }
                )
        return None
