# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Any, Dict, Tuple

from aworld.config import ToolConfig
from aworld.core.common import ActionModel, Observation
from aworld.core.envs.llm_executor import LLMToolActionExecutor
from aworld.core.envs.tool import Tool


class ToolAgentBase(Tool[Observation, ActionModel]):
    """A base class for tool agents.
    This class provides a foundation for creating agents that interact with tools.
    Attributes:
        conf (ToolConfig): The configuration for the tool.
        initialized (bool): Flag indicating whether the tool has been initialized.
        action_executor (LLMToolActionExecutor): The LLM tool action executor.
        content (Any): The content of the tool.
        action_ctx (Any): The context of the tool action.
        cur_observation (Observation): The current observation of the tool.
        _finish (bool): Flag indicating whether the tool has finished.
        step_finished (bool): Flag indicating whether the current step is finished.
    """

    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        """Init tool agent."""
        super().__init__(conf, **kwargs)
        self.content = None
        self.action_ctx = None
        self.cur_observation = None
        self.init()
        self._finish = False
        self.step_finished = True

    def reset(
        self, *, seed: int | None = None, options: Dict[str, str] | None = None
    ) -> Tuple[Observation, dict[str, Any]]:
        super().reset(seed=seed, options=options)

        self.close()
        self.step_finished = True
        return self._get_observation(), {}

    def init(self) -> None:
        """Initialize the tool agent.

        This method sets up the LLM tool action executor and marks the tool as initialized.
        """
        tool_name = self.name
        llm_provider = self.dict_conf.get("LLM_PROVIDER", "openai")
        llm_model_name = self.dict_conf.get("LLM_MODEL_NAME", "gpt-4o")
        self.action_executor = LLMToolActionExecutor(
            tool_name,
            llm_provider,
            llm_model_name,
        )

        self.initialized = True

    def _get_observation(self) -> Observation:
        return Observation(
            **{"dom_tree": "", "image": "", "action_result": [], "info": {}}
        )

    def close(self) -> None:
        pass

    def finished(self) -> bool:
        return self.step_finished

    def step(
        self, actions: list[ActionModel], **kwargs
    ) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        if not self.initialized:
            raise RuntimeError("Call init first before calling step.")

        reward = 0
        fail_error = ""
        action_result = None

        try:
            action_result, self.action_ctx = self.action_executor.execute_action(
                actions, **kwargs
            )
            reward = 1
        except (ValueError, IOError, RuntimeError) as e:
            fail_error = str(e)

        terminated = kwargs.get("terminated", False)
        if action_result:
            for res in action_result:
                if res.is_done:
                    terminated = res.is_done
                    self._finish = True

        info = {"exception": fail_error}

        observation = self._get_observation()
        if action_result:
            observation.action_result = action_result
            observation.content = action_result[-1].content
        self.cur_observation = observation
        return (observation, reward, terminated, kwargs.get("truncated", False), info)
