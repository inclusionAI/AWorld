import json
import os
from pathlib import Path
import random
import sys
import unittest

from aworld.planner.plan import DefaultPlanner, PlannerOutputParser

# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from examples.tools.common import Tools

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig

from tests.base_test import BaseTest


class TestPlanner(BaseTest):

    # def test_planner(self):
    #     # 创建一个基本的planner实例（不使用LLM模型）
    #     planner = BuiltInPlanner(llm_model=LLMModel(model_name=self.mock_model_name, base_url=self.mock_base_url, api_key=self.mock_api_key))

    #     # 创建一个简单的context
    #     context = Context()
    #     context.context_info.update(
    #         handoffs={
    #             "search_agent": {"description": "搜索工具"},
    #             "summary_agent": {"description": "总结工具"}
    #         }
    #     )

    #     # 测试生成计划
    #     plan = planner.plan(context=context, input="分析苹果公司的发展历程")
    #     print("plan", plan)

    #     # 验证计划生成的基本属性
    #     self.assertIsNotNone(plan)
    #     self.assertTrue(len(plan.steps) > 0)

    def test_planner_agent(self):
        agent_id = "id"
        planner = DefaultPlanner()
        agent = Agent(
            agent_id=agent_id,
            conf=AgentConfig(
                llm_model_name=self.mock_model_name,
                llm_base_url=self.mock_base_url,
                llm_api_key=self.mock_api_key
            ),
            name="planner_agent",
            planner=planner,
            resp_parse_func=PlannerOutputParser(agent_id).parse,
        )

        agent2 = Agent(
            conf=AgentConfig(
                llm_model_name=self.mock_model_name,
                llm_base_url=self.mock_base_url,
                llm_api_key=self.mock_api_key
            ),
            name="agent2",
            system_prompt="you are a search agent",
            tool_names=[Tools.SEARCH_API.value]
        )
        self.run_multi_agent(input="分析苹果公司的发展历程", agent1=agent, agent2=agent2)


if __name__ == "__main__":
    unittest.main()
