# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import subprocess
import os
import signal
import sys
from typing import Any, Dict, Tuple, List

from aworld.config.common import Tools
from aworld.config.conf import ToolConfig
from aworld.config.tool_action import ShellAction
from aworld.core.common import ActionModel, Observation, ActionResult
from aworld.core.envs.tool import Tool, AgentInput, ToolFactory
from aworld.logs.util import logger
from aworld.mcp.tools import MCPToolExecutor
from aworld.virtual_environments.utils import build_observation


@ToolFactory.register(name=Tools.MCP.value,
                      desc="mcp execute tool")
class McpTool(Tool[Observation, List[ActionModel]]):
    """
    used to execute shell commands, providing initialization, execution, and exit functions.
    """

    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        """
        Initialize the McpTool
        Args:
            conf: tool config
            **kwargs: -
        """
        super(McpTool, self).__init__(conf, **kwargs)
        self.type = "function"
        self.processes = []

    def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
        AgentInput, dict[str, Any]]:
        """
        Reset the executor
        Args:
            seed: -
            options: -

        Returns:
            AgentInput, dict[str, Any]: -
        """
        self.processes = []
        self._finished = False
        return build_observation(observer=self.name(),ability=""), {}

    def close(self) -> None:
        """
        Close the executor
        Returns:
            None
        """
        self.processes = []
        self._finished = True

    def step(self,
             actions: list[ActionModel],
             **kwargs) -> Tuple[Observation, float, bool, bool, dict[str, Any]]:
        """
        Step the executor
        Args:
            actions: actions
            **kwargs: -
        Returns:
            Observation, float, bool, bool, dict[str, Any]: -
        """

        self._finished = False
        reward = 0
        fail_error = ""
        observation = build_observation(observer=self.name(),
                                        ability="")
        terminated = kwargs.get("terminated", False)
        try:
            if not actions:
                return (observation, reward,
                        kwargs.get("terminated",
                                   False), kwargs.get("truncated", False), {
                            "exception": "actions is empty"
                        })
            mcp_actions = []
            for action in actions:
                tool_name = action.tool_name
                if Tools.MCP.value != tool_name:
                    logger.warning(f"Unsupported tool: {tool_name}")
                    continue
                full_tool_name = action.action_name
                names = full_tool_name.split("__")
                if len(names) < 2:
                    continue
                action.action_name= names[1]
                action.tool_name = names[0]
                mcp_actions.append(action)
            if not mcp_actions:
                return (observation, reward,
                        terminated,
                        kwargs.get("truncated", False),
                        {"exception": "actions is empty"})
            mcp_executor = MCPToolExecutor()
            # Initialize the MCPToolExecutor before calling step
            mcp_executor._load_mcp_config()
            observation, reward, terminated, _, info = mcp_executor.step(mcp_actions)
            reward = 1
        except Exception as e:
            fail_error = str(e)
        finally:
            self._finished = True

        info = {"exception": fail_error}
        info.update(kwargs)
        return (observation,
                reward,
                terminated,
                kwargs.get("truncated", False),
                info)
