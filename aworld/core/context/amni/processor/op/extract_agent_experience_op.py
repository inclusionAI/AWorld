from typing import Any, Dict, Optional

from ... import ApplicationContext
from .op_factory import memory_op
from .langextract_op import LangExtractOp
from ...prompt.prompts import AGENT_EXPERIENCE_FEW_SHOTS,AMNI_CONTEXT_PROMPT


from aworld.memory.models import AgentExperience


@memory_op("extract_agent_experience")
class AgentExperienceLangExtractOp(LangExtractOp[AgentExperience]):
    """
    Concrete implementation for agent experience extraction using langextract
    """

    def __init__(self, prompt: str = AMNI_CONTEXT_PROMPT["AGENT_EXPERIENCE_EXTRACTION_PROMPT"], 
                 few_shots=AGENT_EXPERIENCE_FEW_SHOTS, **kwargs):
        """
        Initialize AgentExperienceLangExtractOp

        Args:
            prompt: Prompt template for agent experience extraction
            few_shots: Few-shot examples for agent experience extraction
            **kwargs: Additional configuration options
        """
        super().__init__("extract_agent_experience", prompt, extraction_classes=["agent_experience"], 
                         few_shots=few_shots, **kwargs)

    def _prepare_extraction_text(self, context: ApplicationContext, agent_id: str) -> str:
        """
        Prepare text for agent experience extraction

        Args:
            context: Application context
            agent_id: Agent identifier

        Returns:
            Formatted text for extraction
        """
        if not agent_id:
            return None
        return (f"\n\nExisted Agent Experience: \n{context.get_agent_experiences()}"
                f"\n\nConversation:\n{context.get_history_desc()}")

    def _build_memory_item(self, extract_data: Dict[str, Any], context: ApplicationContext, agent_id: str) -> Optional[AgentExperience]:
        """
        Build AgentExperience memory item from extracted data

        Args:
            extract_data: Extracted data from LLM
            context: Application context
            agent_id: Agent identifier

        Returns:
            AgentExperience object or None if data is invalid
        """
        if extract_data:
            skill = extract_data.get("skill")
            actions = extract_data.get("actions")
            if skill and actions and isinstance(actions, list):
                return AgentExperience(
                    agent_id=context.task_input_object.model if agent_id == "default" else agent_id,
                    skill=skill,
                    actions=actions,
                    metadata={
                        "agent_name": context.task_input_object.model if agent_id == "default" else agent_id,
                        "agent_id": context.task_input_object.model if agent_id == "default" else agent_id,
                        "task_id": context.task_id,
                        "session_id": context.session_id,
                    }
                )
        return None
