# coding: utf-8
from typing import Dict, Any, List, Union

from aworld.agents.travel.prompts import search_prompt, search_sys_prompt, search_output_prompt
from aworld.agents.travel.utils import parse_result
from aworld.config.common import Agents, Tools
from aworld.config.conf import AgentConfig
from aworld.core.agent.base import AgentFactory, Agent
from aworld.core.common import Observation, ActionModel


# Step1
@AgentFactory.register(name=Agents.SEARCH.value, desc="search agent")
class SearchAgent(Agent):

    def __init__(self, conf: AgentConfig, **kwargs):
        super(SearchAgent, self).__init__(conf, **kwargs)
        # Step 2
        # Also can add other agent as tools (optional, it can be ignored if the interacting agent is deterministic.),
        # we only use search api tool for example.
        if Tools.SEARCH_API.value not in self.tool_names:
            self.tool_names.append(Tools.SEARCH_API.value)

    def policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> Union[
        List[ActionModel], None]:
        if observation.action_result and observation.action_result[0].is_done:
            self._finished = True
            return [ActionModel(tool_name="[done]", policy_info=observation.content)]

        tool_desc = self.desc_transform()
        messages = [{'role': 'system', 'content': search_sys_prompt},
                    {'role': 'user',
                     'content': search_prompt.format(task=observation.content,
                                                     tool_desc=tool_desc) + search_output_prompt}]

        llm_result = self.llm.invoke(
            input=messages,
        )
        tool_calls = llm_result.content
        return parse_result(tool_calls)
