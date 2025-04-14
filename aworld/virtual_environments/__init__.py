# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.virtual_environments.android.android import AndroidTool
from aworld.virtual_environments.apis.search_api import SearchTool
from aworld.virtual_environments.browsers.browser import BrowserTool
from aworld.virtual_environments.browsers.async_browser import BrowserTool as ABrowserTool
from aworld.virtual_environments.document.document import DocumentTool
from aworld.virtual_environments.gym.openai_gym import OpenAIGym
from aworld.virtual_environments.gym.async_openai_gym import OpenAIGym as AOpenAIGym
from aworld.virtual_environments.interpreters.python_tool import PythonTool
from aworld.virtual_environments.terminals.shell_tool import ShellTool
from aworld.virtual_environments.travel.html import HtmlTool
from aworld.virtual_environments.mcp.mcp_tool import McpTool

from aworld.virtual_environments.android.action.actions import *
from aworld.virtual_environments.apis.actions import *
from aworld.virtual_environments.browsers.action.actions import *
from aworld.virtual_environments.document.actions import *
from aworld.virtual_environments.gym.actions import *
from aworld.virtual_environments.terminals.actions import *
from aworld.virtual_environments.travel.actions import *

from aworld.core.envs.action_factory import ActionFactory
from aworld.logs.util import logger
from aworld.core.envs.tool import ToolFactory


def tool_action_desc():
    """Utility method of generate description of tools and their actions.

    The standard protocol can be transformed based on the API of different llm.
    Define as follows:
    ```
    {
        "tool_name": {
            "desc": "A toolkit description.",
            "actions": [
                {
                    "name": "action name",
                    "desc": "action description.",
                    "params": {
                        "param_name": {
                            "desc": "param description.",
                            "type": "param type, such as int, str, etc.",
                            "required": True | False
                        }
                    }
                }
            ]
        }
    }
    ```
    """

    def process(action_info):
        action_dict = dict()
        action_dict["name"] = action_info.name
        action_dict["desc"] = action_info.desc
        action_dict["params"] = dict()

        for k, v in action_info.input_params.items():
            params_dict = v.model_dump()
            params_dict.pop("name")
            action_dict["params"][k] = params_dict
        return action_dict

    descs = dict()
    for tool in ToolFactory:
        tool_val_dict = dict()
        descs[tool] = tool_val_dict

        tool_val_dict["desc"] = ToolFactory.desc(tool)
        tool_action = ToolFactory.get_tool_action(tool)
        actions = []
        action_names = ActionFactory.get_actions_by_tool(tool_name=tool)
        if action_names:
            for action_name in action_names:
                info = tool_action.get_value_by_name(action_name)
                if not info:
                    logger.warning(f"{action_name} can not find in {tool}, please check it.")
                    continue
                try:
                    action_dict = process(info)
                except:
                    logger.warning(f"{action_name} process fail.")
                    action_dict = dict()
                actions.append(action_dict)
        elif tool_action:
            for k, info in tool_action.__members__.items():
                action_dict = process(info.value)
                actions.append(action_dict)
        else:
            logger.warning(f"{tool} no action!")
        tool_val_dict["actions"] = actions
    return descs
