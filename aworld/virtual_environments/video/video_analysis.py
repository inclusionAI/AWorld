# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Any, Dict, Tuple

from aworld.config import ToolConfig
from aworld.core.common import ActionModel, Observation, Tools
from aworld.core.envs.llm_executor import LLMToolActionExecutor
from aworld.core.envs.tool import Tool, ToolFactory
from aworld.core.envs.tool_action import VideoAnalysisAction


@ToolFactory.register(
    name=Tools.VIDEO_ANALYSIS.value,
    desc="Perform transcription, analysis or summarization over the given video filepath or url",
    supported_action=VideoAnalysisAction,
)
class VideoAnalysisTool(Tool[Observation, ActionModel]):
    """A tool for performing video analysis tasks like transcription and content analysis.

    This tool inherits from the base Tool class and specializes in processing video
    through transcription and performing analysis tasks on video files
    provided either as file paths or URLs.

    Attributes:
        content: Stores the processed content from the video
        video_base64: Stores the base64 encoded video data
        cur_observation: Keeps track of the current observation state
        step_finished: Indicates if the current processing step is complete
    """

    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        """Init video tool."""
        super().__init__(conf, **kwargs)
        self.content = None
        self.video_frames = None
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
        """Initialize the video analysis tool.

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
            action_result, self.video_frames = self.action_executor.execute_action(
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
