# coding: utf-8
from typing import Dict, Any, List, Union

from aworld.agents.travel.prompts import write_prompt, write_sys_prompt, write_output_prompt
from aworld.agents.travel.utils import parse_result
from aworld.config.conf import AgentConfig
from aworld.core.agent.base import AgentFactory, Agent
from aworld.core.common import Observation, ActionModel


@AgentFactory.register(name='write_agent', desc="write agent")
class WriteAgent(Agent):
    def __init__(self, conf: AgentConfig, **kwargs):
        super(WriteAgent, self).__init__(conf, **kwargs)
        self.tool_names.append('html')

    def policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> Union[
        List[ActionModel], None]:
        if observation.action_result and observation.action_result[0].is_done:
            self._finished = True
            return [ActionModel(tool_name="[done]", policy_info=observation.content)]

        tool_desc = self.desc_transform()

        messages = [{'role': 'system', 'content': write_sys_prompt},
                    {'role': 'user', 'content': write_prompt.format(task=observation.content['task'],
                                                                    reference=observation.content['refer'],
                                                                    tool_desc=tool_desc) + write_output_prompt}]
        llm_result = self.llm.invoke(
            input=messages,
        )
        tool_calls = llm_result.content
        return parse_result(tool_calls)
