
import os
import random
import sys
import json
from pathlib import Path
import unittest

# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from aworld.core.context.base import Context
from aworld.config.conf import AgentConfig, ContextRuleConfig, ModelConfig
from aworld.agents.llm_agent import Agent
from aworld.runner import Runners
from aworld.core.agent.swarm import Swarm, TeamSwarm
from aworld.core.task import Task


class BaseTest(unittest.TestCase):

    def setUp(self):
        """Load test configuration from JSON file"""
        config_path = Path(__file__).parent / "test_config.json"
        try:
            with open(config_path, 'r') as f:
                self.test_config = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load test_config.json: {e}")
            self.test_config = {
                "MODEL_NAME": "qwen/qwen3-1.7b",
                "BASE_URL": "http://localhost:1234/v1",
                "API_KEY": "lm-studio"
            }
        """Set up test fixtures"""
        # Initialize from test_config
        self.mock_model_name = self.test_config.get("MODEL_NAME")
        self.mock_base_url = self.test_config.get("BASE_URL")
        self.mock_api_key = self.test_config.get("API_KEY")
        self.mock_llm_config = ModelConfig(
            llm_model_name=self.mock_model_name,
            llm_base_url=self.mock_base_url,
            llm_api_key=self.mock_api_key
        )
        
        # Set environment variables
        os.environ["LLM_API_KEY"] = self.mock_api_key
        os.environ["LLM_BASE_URL"] = self.mock_base_url
        os.environ["LLM_MODEL_NAME"] = self.mock_model_name

    def fail(self, msg=None):
        """Fail immediately with the given message"""
        raise AssertionError(msg or "Test failed")

    def init_agent(self,
                   config_type: str = "1",
                   context_rule: ContextRuleConfig = None,
                   name: str = "my_agent" + str(random.randint(0, 1000000))):
        if config_type == "1":
            conf = AgentConfig(
                llm_model_name=self.mock_model_name,
                llm_base_url=self.mock_base_url,
                llm_api_key=self.mock_api_key
            )
        else:
            conf = AgentConfig(
                llm_config=self.mock_llm_config
            )
        return Agent(
            conf=conf,
            name=name,
            system_prompt="You are a helpful assistant.",
            agent_prompt="make a joke.",
            context_rule=context_rule
        )

    def run_agent(self, input, agent: Agent):
        swarm = Swarm(agent, max_steps=1)
        return Runners.sync_run(
            input=input,
            swarm=swarm
        )

    def run_multi_agent_as_team(self, input, agent1: Agent, agent2: Agent):
        swarm = TeamSwarm(agent1, agent2, max_steps=1)
        return Runners.sync_run(
            input=input,
            swarm=swarm
        )

    def run_task(self, context: Context, agent: Agent):
        swarm = Swarm(agent, max_steps=1)
        task = Task(input="""What is an agent.""",
                    swarm=swarm, context=context)
        return Runners.sync_run_task(task)
