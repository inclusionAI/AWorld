# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import base64
import json
import os
import subprocess
import tempfile
from typing import Any, Dict, Tuple
from urllib.parse import urlparse

from pydantic import BaseModel

from aworld.config import ToolConfig
from aworld.core.common import ActionModel, ActionResult, Observation, Tools
from aworld.core.envs.tool import Tool, ToolFactory
from aworld.core.envs.tool_action import ImageAnalysisAction
from aworld.logs.util import logger
from aworld.models.llm import get_llm_model
from aworld.utils import import_packages
from aworld.virtual_environments.image.llm_executor import LLMToolActionExecutor
from aworld.virtual_environments.image.prompts import IMAGE_OCR, IMAGE_REASONING


@ToolFactory.register(
    name=Tools.IMAGE_ANALYSIS.value,
    desc="Perform OCR or reasoning over the given image filepath or url",
    supported_action=ImageAnalysisAction,
)
class ImageAnalysisTool(Tool[Observation, ActionModel]):
    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        """Init image tool."""
        super(ImageAnalysisTool, self).__init__(conf, **kwargs)
        self._observation_space = self.observation_space()
        self._action_space = self.action_space()
        self.cur_observation = None
        self.content = None
        self.init()
        self.step_finished = True

    def observation_space(self):
        pass

    def action_space(self):
        pass

    def reset(
        self, *, seed: int | None = None, options: Dict[str, str] | None = None
    ) -> Tuple[Observation, dict[str, Any]]:
        super().reset(seed=seed, options=options)

        self.close()
        self.step_finished = True
        return self._get_observation(), {}

    def init(self) -> None:
        self.action_executor = LLMToolActionExecutor()
        self.initialized = True

    def _get_observation(self):
        return Observation(
            **{"dom_tree": "", "image": "", "action_result": [], "info": {}}
        )

    def close(self) -> None:
        if hasattr(self, "context") and self.context:
            self.context.close()

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
            action_result, self.image_base64 = self.action_executor.execute_action(
                actions, **kwargs
            )
            reward = 1
        except Exception as e:
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
