# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
from typing import Any, List, Tuple

from aworld.config.conf import ModelConfig
from aworld.core.common import ActionModel, ActionResult
from aworld.core.envs.action_factory import ActionFactory
from aworld.core.envs.tool import ToolActionExecutor
from aworld.logs.util import logger
from aworld.models.llm import get_llm_model


class LLMToolActionExecutor(ToolActionExecutor):
    """
    LLM Tool Action Executor for executing LLM-based tool action sequences.

    Inherits from ToolActionExecutor base class, implements abstract methods
    async_execute_action and execute_env_action. Primarily handles and executes
    LLM-related tool actions.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # check environment variables valid
        llm_provider = os.getenv("LLM_PROVIDER", "openai")
        llm_model_name = os.getenv("LLM_MODEL_NAME", "gpt-4o")
        llm_api_key = os.getenv("LLM_API_KEY", "")
        llm_base_url = os.getenv("LLM_BASE_URL", "")

        assert llm_api_key, "LLM_API_KEY is required"
        assert llm_base_url, "LLM_BASE_URL is required"

        # set llm model
        llm_config = ModelConfig(
            llm_provider=llm_provider,
            llm_model_name=llm_model_name,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
        )
        self.llm = get_llm_model(llm_config)

    def _exec(
        self, action_model: ActionModel, **kwargs
    ) -> List[Tuple[ActionResult, Any]]:
        action_name = action_model.action_name
        if action_name not in ActionFactory:
            action_name = action_model.tool_name + action_model.action_name
            if action_name not in ActionFactory:
                raise ValueError(f"Action {action_name} not found")

        action = ActionFactory(action_name)
        action_result, ctx = action.act(action_model, llm=self.llm, **kwargs)
        logger.info("%s execute finished", action_name)
        return action_result, ctx

    def execute_action(
        self, actions: List[ActionModel], **kwargs
    ) -> List[Tuple[ActionResult, Any]]:
        """Execute the specified android action sequence by agent policy.

        Args:
            actions: Tool action sequence.

        Returns:
            action_results: LLM action result list.
            ctx: action context object.
        """
        action_results = []
        for action in actions:
            action_result, ctx = self._exec(action, **kwargs)
            action_results.append(action_result)
        return action_results, ctx

    async def async_execute_action(
        self, actions: List[ActionModel], **kwargs
    ) -> List[Tuple[ActionResult, Any]]:
        """async execution

        Args:
            actions: a series of action models.
            **kwargs: extra keyword arguments.

        Returns:
            action_results: LLM action result list.
            ctx: action context object.
        """
        return self.execute_action(actions, **kwargs)
