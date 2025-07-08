
import os
import random
import sys
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from tests.base_test import BaseTest
from aworld.runners.hook.hook_factory import HookFactory
from aworld.core.context.base import Context
from aworld.config.conf import AgentConfig, ContextRuleConfig, ModelConfig, OptimizationConfig, LlmCompressionConfig
from aworld.agents.llm_agent import Agent
from aworld.runner import Runners
from aworld.core.agent.swarm import Swarm
from aworld.core.task import Task

class TestContextManagement(BaseTest):
    """Test cases for Context Management system based on README examples"""

    def __init__(self):
        """Set up test fixtures"""
        self.mock_model_name = "qwen/qwen3-1.7b"
        self.mock_base_url = "http://localhost:1234/v1"
        self.mock_api_key = "lm-studio"
        os.environ["LLM_API_KEY"] = self.mock_api_key
        os.environ["LLM_BASE_URL"] = self.mock_base_url
        os.environ["LLM_MODEL_NAME"] = self.mock_model_name

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
            agent_prompt="make a joke.",
            context_rule=context_rule
        )

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
        task = Task(input="""What is an agent.""",
                    swarm=swarm, context=context)
        return Runners.sync_run_task(task)

    def test_default_context_configuration(self):

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
        response = self.run_agent(
            input="""What is an agent. describe within 20 words""", agent=mock_agent)

        self.assertIsNotNone(response.answer)
        self.assertEqual(
            mock_agent.conf.llm_config.llm_model_name, self.mock_model_name)

        # Test default context rule behavior
        self.assertIsNotNone(mock_agent.context_rule)
        self.assertIsNotNone(
            mock_agent.context_rule.optimization_config)

    def test_custom_context_configuration(self):
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

        response = self.run_agent(
            input="""describe What is an agent in details""", agent=mock_agent)
        self.assertIsNotNone(response.answer)

        # Test configuration values
        self.assertTrue(
            mock_agent.context_rule.optimization_config.enabled)
        self.assertTrue(
            mock_agent.context_rule.llm_compression_config.enabled)

    def test_multi_agent_state_trace(self):
        class StateModifyAgent(Agent):
            async def async_policy(self, observation, info=None, **kwargs):
                result = await super().async_policy(observation, info, **kwargs)
                self.context.context_info.set('policy_executed', True)
                return result

        class StateTrackingAgent(Agent):
            async def async_policy(self, observation, info=None, **kwargs):
                result = await super().async_policy(observation, info, **kwargs)
                assert self.context.context_info.get('policy_executed', True)
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
        self.assertIsNotNone(response.answer)

        # Verify state changes after execution
        self.assertTrue(custom_agent.context.context_info.get('policy_executed', True))

    def test_multi_task_state_trace(self):
        context = Context()
        task = Task(input="What is an agent.", context=context)
        new_context = task.context.deep_copy()
        new_context.context_info.update({"hello": "world"})
        self.run_task(context=new_context, agent=self.init_agent("1"))
        self.assertEqual(new_context.context_info.get("hello"), "world")
        
        task.context.merge_context(new_context)
        self.assertEqual(task.context.context_info.get("hello"), "world")


class TestHookSystem(TestContextManagement):
    def __init__(self):
        super().__init__()

    def test_hook_registration(self):
        from tests.test_llm_hook import TestPreLLMHook, TestPostLLMHook
        """Test hook registration and retrieval"""
        # Test that hooks are registered in _cls attribute
        self.assertIn("TestPreLLMHook", HookFactory._cls)
        self.assertIn("TestPostLLMHook", HookFactory._cls)

        # Test hook creation using __call__ method
        pre_hook = HookFactory("TestPreLLMHook")
        post_hook = HookFactory("TestPostLLMHook")

        self.assertIsInstance(pre_hook, TestPreLLMHook)
        self.assertIsInstance(post_hook, TestPostLLMHook)

    def test_hook_execution(self):
        from tests.test_llm_hook import TestPreLLMHook, TestPostLLMHook

        mock_agent = self.init_agent("1")
        response = self.run_agent(
            input="""What is an agent. describe within 20 words""", agent=mock_agent)
        self.assertIsNotNone(response.answer)

    def test_task_context_transfer(self):
        from tests.test_context_hook import CheckContextPreLLMHook

        mock_agent = self.init_agent("1")
        context = Context()
        context.context_info.update({"task": "What is an agent."})
        self.run_task(context=context, agent=mock_agent)


if __name__ == '__main__':
    testContextManagement = TestContextManagement()
    testContextManagement.test_default_context_configuration()
    testContextManagement.test_custom_context_configuration()
    testContextManagement.test_multi_agent_state_trace()
    testContextManagement.test_multi_task_state_trace()
    testHookSystem = TestHookSystem()
    testHookSystem.test_hook_registration()
    testHookSystem = TestHookSystem()
    testHookSystem.test_hook_execution()
    testHookSystem = TestHookSystem()
    testHookSystem.test_task_context_transfer()
