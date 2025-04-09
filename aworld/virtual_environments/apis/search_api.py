# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import json
from typing import List, Tuple, Dict, Any

from aworld.config.common import Tools
from aworld.config.conf import ToolConfig
from aworld.core.envs.tool import Tool, ToolFactory
from aworld.config.tool_action import SearchAction
from aworld.core.common import Observation, ActionModel, ActionResult
from aworld.logs.util import logger
from aworld.virtual_environments.utils import build_observation


@ToolFactory.register(name=Tools.SEARCH_API.value,
                      desc="search tool",
                      supported_action=SearchAction,
                      conf_file_name=f'{Tools.SEARCH_API.value}_tool.yaml')
class SearchTool(Tool[Observation, List[ActionModel]]):
    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        super(SearchTool, self).__init__(conf, **kwargs)

    def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
        Observation, dict[str, Any]]:
        # from options obtain user query
        return build_observation(observer=self.name(),
                                 ability='',
                                 content=options.get("query", None) if options else None), {}

    def step(self, action: List[ActionModel], **kwargs) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        reward = 0
        fail_error = ""
        action_result = None

        invalid_acts: List[int] = []
        for i, act in enumerate(action):
            if act.tool_name != Tools.SEARCH_API.value:
                logger.warning(f"tool {act.tool_name} is not a search api!")
                invalid_acts.append(i)

        if invalid_acts:
            for i in invalid_acts:
                action[i] = None

        resp = ""
        try:
            action_result, resp = self.action_executor.execute_action(action, **kwargs)
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
        info.update(kwargs)
        if resp:
            resp = json.dumps(resp)
        else:
            resp = action_result[0].content

        action_result = [ActionResult(content=resp, keep=True, is_done=True)]
        observation = build_observation(observer=self.name(),
                                        ability=action[-1].action_name,
                                        content=resp)
        observation.action_result = action_result
        return (observation,
                reward,
                terminated,
                kwargs.get("truncated", False),
                info)

    def close(self) -> None:
        pass

    def finished(self) -> bool:
        # one time
        return True
