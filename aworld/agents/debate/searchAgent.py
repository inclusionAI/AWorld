# coding: utf-8
import os
import json
from typing import Dict, Any, List, Union

from aworld.config.conf import AgentConfig
from aworld.core.agent.base import AgentFactory, BaseAgent
from aworld.core.common import Agents, Observation, ActionModel, Tools
from aworld.core.envs.tool_desc import get_tool_desc_by_name
from aworld.models.utils import tool_desc_transform
from aworld.core.envs.tool import ToolFactory
from aworld.config.conf import load_config


sys_prompt = "You are a helpful search agent."

prompt = """
Please act as a search agent, constructing appropriate keywords and search terms, using search toolkit to collect relevant information, including urls, webpage snapshots, etc.

Here are the question: {task}

Here are the tool you can use: {tool_desc}

please only use one action complete this task, at least results 6 pages.
"""

response_format = """1. RESPONSE FORMAT: You must ALWAYS respond with valid JSON in this exact format:
{"action":[{{"one_action_name": {{// action-specific parameter}}}}, // ... more actions in sequence]}
"""

# Step1
@AgentFactory.register(name=Agents.SEARCH.value, desc="search agent")
class SearchAgent(BaseAgent):

    def __init__(self, conf: AgentConfig, **kwargs):
        super(SearchAgent, self).__init__(conf, **kwargs)
        # Step 2
        # Also can add other agent as tools (optional, it can be ignored if the interacting agent is deterministic.),
        # we only use search api tool for example.
        self.tool_desc = tool_desc_transform({Tools.SEARCH_API.value: get_tool_desc_by_name(Tools.SEARCH_API.value)})

    # Step3
    def name(self) -> str:
        return Agents.SEARCH.value

    def policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> Union[
        List[ActionModel], None]:
        if observation.action_result is not None and len(observation.action_result)!=0 and observation.action_result[0].is_done:
            self._finished = True

            return [ActionModel(tool_name="[done]", policy_info=observation.content)]

        messages = [{'role': 'system', 'content': sys_prompt},
                    {'role': 'user', 'content': prompt.format(task=observation.content, tool_desc=self.tool_desc) + response_format}]

        print("messages:", messages)

        llm_result = self.llm.invoke(
            input=messages,
        )
        tool_calls = llm_result.content
        print("tool_calls:", tool_calls)

        # 解析LLM返回结果
        return self._result(tool_calls)

    def _result(self, data):

        data = json.loads(data.replace("```json","").replace("```",""))
        actions = data.get("action", [])
        parsed_results = []

        for action in actions:
            # 遍历 action 中的键值对
            for key, value in action.items():
                # 分割 action_name 和 tool_name
                if "__" in key:
                    tool_name, action_name = key.split("__", 1)

                # 提取 params
                params = value

                # 将解析结果存入列表
                parsed_results.append(ActionModel(tool_name=tool_name,
                                       action_name=action_name,
                                       params=params))
        print("parsed_results:", parsed_results)
        return parsed_results

if __name__ == '__main__':
    agentConfig = AgentConfig(
        llm_provider="chatopenai",
        llm_model_name="gpt-4o",
        llm_base_url="http://localhost:5000",
        llm_api_key="dummy-key",
        # llm_api_key="sk-zk2a78b5ba086a2a86115e26974ec33d50b67e394fa5fb91",  ## 花钱 zhitian
        # llm_base_url = "https://api.zhizengzeng.com/v1",  ## 花钱 zhitian
    )

    # zhitian
    # os.environ["GOOGLE_API_KEY"] = "AIzaSyAqNaFl2Ly-sBiLXcxA68ZLWwLF0Yc99V0"
    # os.environ["GOOGLE_ENGINE_ID"] = "77bfce5ddc990489c"

    # wenwen
    os.environ["GOOGLE_API_KEY"] = "AIzaSyBv9Jd-l5MJcY4ZY3n1i2SB_H5px3c3_xs"
    os.environ["GOOGLE_ENGINE_ID"] = "66ef6e0ccdfb44d77"


    # Google api key="AIzaSyBv9Jd-l5MJcY4ZY3n1i2SB_H5px3c3_xs"
    # SEARCH_ENGINE_ID="66ef6e0ccdfb44d77"
    searchagent = SearchAgent(agentConfig)

    goal = "The best basketball player in China."
    observation = Observation(content=goal)
    while True:
        policy = searchagent.policy(observation=observation)
        # policy =  [ActionModel(tool_name='search_api', agent_name=None, action_name='wiki', params={'query': 'best basketball player in China', 'num_result_pages': '6'}, policy_info=None)]

        print("policy:", policy)

        if policy[0].tool_name == '[done]':
            break

        tool = ToolFactory(policy[0].tool_name, conf = load_config(f"{policy[0].tool_name}.yaml"))

        observation, reward, terminated, _, info = tool.step(policy)

        print("observation:", observation)

    print(policy[0].policy_info)