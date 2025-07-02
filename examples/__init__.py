import os
import random
import sys
from pathlib import Path

from aworld.core.task import Task
from aworld.core.agent.base import AgentFactory
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, ContextRuleConfig, ModelConfig, OptimizationConfig, LlmCompressionConfig
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.runners.hook.hooks import PreLLMCallHook, PostLLMCallHook
from aworld.runners.hook.hook_factory import HookFactory
from aworld.utils.common import convert_to_snake


class ContextManagement():
    """Test cases for Context Management system based on README examples"""

    def init_agent(self, config_type: str = "1", context_rule: ContextRuleConfig = None):
        if config_type == "1":
            conf = AgentConfig(
                llm_model_name=self.mock_model_name,
                llm_base_url=self.mock_base_url,
                llm_api_key=self.mock_api_key
            )
        else:
            conf = AgentConfig(
                llm_config=ModelConfig(
                    llm_model_name=self.mock_model_name,
                    llm_base_url=self.mock_base_url,
                    llm_api_key=self.mock_api_key
                )
            )
        return Agent(
            conf=conf,
            name="my_agent" + str(random.randint(0, 1000000)),
            system_prompt="You are a helpful assistant.",
            agent_prompt="You are a helpful assistant.",
            context_rule=context_rule
        )

    def __init__(self):
        """Set up test fixtures"""
        self.mock_model_name = "gpt-4o"
        self.mock_base_url = "http://localhost:34567"
        self.mock_api_key = "lm-studio"
        os.environ["LLM_API_KEY"] = self.mock_api_key
        os.environ["LLM_BASE_URL"] = self.mock_base_url
        os.environ["LLM_MODEL_NAME"] = self.mock_model_name

    class _AssertRaisesContext:
        """Context manager for assertRaises"""

        def __init__(self, expected_exception):
            self.expected_exception = expected_exception

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            if exc_type is None:
                raise AssertionError(
                    f"Expected {self.expected_exception.__name__} to be raised, but no exception was raised")
            if not issubclass(exc_type, self.expected_exception):
                raise AssertionError(
                    f"Expected {self.expected_exception.__name__} to be raised, but got {exc_type.__name__}: {exc_value}")
            return True  # Suppress the exception

    def fail(self, msg=None):
        """Fail immediately with the given message"""
        raise AssertionError(msg or "Test failed")

    def run_agent(self, input, agent: Agent):
        swarm = Swarm(agent, max_steps=1)
        print('swarm ', swarm)
        return Runners.sync_run(
            input=input,
            swarm=swarm
        )

    def run_multi_agent(self, input, agent1: Agent, agent2: Agent):
        swarm = Swarm(agent1, agent2, max_steps=1)
        return Runners.sync_run(
            input=input,
            swarm=swarm
        )

    def run_task(self, context: Context, agent: Agent):
        swarm = Swarm(agent, max_steps=1)
        task = Task(input="""What is an agent.""", swarm=swarm, context=context)
        result = Runners.sync_run_task(task)
        print("----------------------------------------------------------------------------------------------")
        print(result)

    def default_context_configuration(self):

        # No need to explicitly configure context_rule, system automatically uses default configuration
        # Default configuration is equivalent to:
        # context_rule=ContextRuleConfig(
        #     optimization_config=OptimizationConfig(
        #         enabled=True,
        #         max_token_budget_ratio=1.0  # Use 100% of context window
        #     ),
        #     llm_compression_config=LlmCompressionConfig(
        #         enabled=False  # Compression disabled by default
        #     )
        # )
        mock_agent = self.init_agent("1")
        response = self.run_agent(input="""What is an agent. describe within 20 words""", agent=mock_agent)

        print(response.answer)

    def custom_context_configuration(self):
        """Test custom context configuration (README Configuration example)"""
        # Create custom context rules
        mock_agent = self.init_agent(context_rule=ContextRuleConfig(
            optimization_config=OptimizationConfig(
                enabled=True,
                max_token_budget_ratio=0.00015
            ),
            llm_compression_config=LlmCompressionConfig(
                enabled=True,
                trigger_compress_token_length=100,
                compress_model=ModelConfig(
                    llm_model_name=self.mock_model_name,
                    llm_base_url=self.mock_base_url,
                    llm_api_key=self.mock_api_key,
                )
            )
        ))

        response = self.run_agent(input="""describe What is an agent in details""", agent=mock_agent)
        print(response.answer)


    def state_management_and_recovery(self):
        class StateModifyAgent(Agent):
            async def async_policy(self, observation, info=None, **kwargs):
                result = await super().async_policy(observation, info, **kwargs)
                self.context.state['policy_executed'] = True
                return result

        class StateTrackingAgent(Agent):
            async def async_policy(self, observation, info=None, **kwargs):
                result = await super().async_policy(observation, info, **kwargs)
                assert self.context.state['policy_executed'] == True
                return result

        # Create custom agent instance
        custom_agent = StateModifyAgent(
            conf=AgentConfig(
                llm_model_name=self.mock_model_name,
                llm_base_url=self.mock_base_url,
                llm_api_key=self.mock_api_key
            ),
            name="state_modify_agent",
            system_prompt="You are a Python expert who provides detailed and practical answers.",
            agent_prompt="You are a Python expert who provides detailed and practical answers.",
        )

        # Create a second agent for multi-agent testing
        second_agent = StateTrackingAgent(
            conf=AgentConfig(
                llm_model_name=self.mock_model_name,
                llm_base_url=self.mock_base_url,
                llm_api_key=self.mock_api_key
            ),
            name="state_tracking_agent",
            system_prompt="You are a helpful assistant.",
            agent_prompt="You are a helpful assistant.",
        )

        response = self.run_multi_agent(
            input="What is an agent. describe within 20 words",
            agent1=custom_agent,
            agent2=second_agent
        )
        print(response.answer)


class TestHookSystem(ContextManagement):

    def __init__(self):
        super().__init__()

    def hook_registration(self):
        """Test hook registration and retrieval"""
        # Test that hooks are registered in _cls attribute
        # Test hook creation using __call__ method
        pre_hook = HookFactory("TestPreLLMHook")
        post_hook = HookFactory("TestPostLLMHook")

    def hook_execution(self):
        mock_agent = self.init_agent("1")
        response = self.run_agent(input="""What is an agent. describe within 20 words""", agent=mock_agent)
        print(response.answer)

    def task_context_transfer(self):

        mock_agent = self.init_agent("1")
        context = Context()
        context.state.update({"task": "What is an agent."})
        self.run_task(context=context, agent=mock_agent)


if __name__ == '__main__':
    testContextManagement = ContextManagement()
    testContextManagement.default_context_configuration()
    testContextManagement.custom_context_configuration()
    testContextManagement.state_management_and_recovery()
    # testHookSystem = TestHookSystem()
    # testHookSystem.hook_registration()
    # testHookSystem = TestHookSystem()
    # testHookSystem.hook_execution()
    # testHookSystem = TestHookSystem()
    # testHookSystem.task_context_transfer()
