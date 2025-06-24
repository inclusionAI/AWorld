import os
import sys
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, ContextRuleConfig, ModelConfig, OptimizationConfig, LlmCompressionConfig
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.runners.hook.hooks import PreLLMCallHook, PostLLMCallHook
from aworld.runners.hook.hook_factory import HookFactory
from aworld.utils.common import convert_to_snake
from tests.base_test import BaseTest

# Test Hook System functionality
@HookFactory.register(name="TestPreLLMHook", desc="Test pre-LLM hook")
class TestPreLLMHook(PreLLMCallHook):
    """Test hook for pre-LLM processing"""
    
    def name(self):
        return convert_to_snake("TestPreLLMHook")
    
    async def exec(self, message: Message, context: Context = None) -> Message:
        agent_context = context.get_agent_context(message.sender)
        if agent_context is not None:
            agent_context.step = 1 
        
        assert agent_context.step == 1 or agent_context.step == 2
        return message


@HookFactory.register(name="TestPostLLMHook", desc="Test post-LLM hook")
class TestPostLLMHook(PostLLMCallHook):
    """Test hook for post-LLM processing"""
    
    def name(self):
        return convert_to_snake("TestPostLLMHook")
    
    async def exec(self, message: Message, context: Context = None) -> Message:
        """Test hook execution with llm_output processing"""
        agent_context = context.get_agent_context(message.sender)
        if agent_context is not None and agent_context.llm_output is not None:
            # Test dynamic prompt adjustment based on LLM output
            if hasattr(agent_context.llm_output, 'content'):
                content = agent_context.llm_output.content.lower()
                if content is not None:
                    agent_context.agent_prompt = "Success mode activated"

        assert agent_context.agent_prompt == "Success mode activated"
        return message


class TestContextManagement(BaseTest):
    """Test cases for Context Management system based on README examples"""

    def __init__(self, config_type: str = "1"):
        """Set up test fixtures"""
        self.mock_model_name = "qwen/qwen3-1.7b"
        self.mock_base_url = "http://localhost:1234/v1"
        self.mock_api_key = "lm-studio"
        os.environ["LLM_API_KEY"] = self.mock_api_key
        os.environ["LLM_BASE_URL"] = self.mock_base_url
        os.environ["LLM_MODEL_NAME"] = self.mock_model_name
        if config_type == "1":
            conf=AgentConfig(
                llm_model_name=self.mock_model_name,
                llm_base_url=self.mock_base_url,
                llm_api_key=self.mock_api_key
            )
        else:
            conf=AgentConfig(
                llm_config=ModelConfig(
                    llm_model_name=self.mock_model_name,
                    llm_base_url=self.mock_base_url,
                    llm_api_key=self.mock_api_key
                )
            )
        self.mock_agent = Agent(
            conf=conf,
            name="my_agent",
            system_prompt="You are a helpful assistant.",
            agent_prompt="You are a helpful assistant.",
        )

    class _AssertRaisesContext:
        """Context manager for assertRaises"""
        def __init__(self, expected_exception):
            self.expected_exception = expected_exception
        
        def __enter__(self):
            return self
        
        def __exit__(self, exc_type, exc_value, traceback):
            if exc_type is None:
                raise AssertionError(f"Expected {self.expected_exception.__name__} to be raised, but no exception was raised")
            if not issubclass(exc_type, self.expected_exception):
                raise AssertionError(f"Expected {self.expected_exception.__name__} to be raised, but got {exc_type.__name__}: {exc_value}")
            return True  # Suppress the exception
    
    def fail(self, msg=None):
        """Fail immediately with the given message"""
        raise AssertionError(msg or "Test failed")
    
    def run_agent(self, input):
        swarm = Swarm(self.mock_agent, max_steps=1)
        return Runners.sync_run(
            input= input,
            swarm=swarm
        )

    def run_multi_agent(self, input, agent1: Agent, agent2: Agent):
        swarm = Swarm(agent1, agent2, max_steps=1)
        return Runners.sync_run(
            input= input,
            swarm=swarm
        )

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
        response = self.run_agent(input= """What is an agent. describe within 20 words""")
        
        self.assertIsNotNone(response.answer)
        self.assertEqual(self.mock_agent.agent_context.model_config.llm_model_name, self.mock_model_name)
        
        # Test default context rule behavior
        self.assertIsNotNone(self.mock_agent.agent_context.context_rule)
        self.assertIsNotNone(self.mock_agent.agent_context.context_rule.optimization_config)

    def test_custom_context_configuration(self):
        """Test custom context configuration (README Configuration example)"""
        # Create custom context rules
        context_rule = ContextRuleConfig(
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
        )
        origin_rule = self.mock_agent.agent_context.context_rule
        self.mock_agent.update_context_rule(context_rule)
        
        response = self.run_agent(input= """describe What is an agent in details""")
        self.assertIsNotNone(response.answer)

        # Test configuration values
        self.assertTrue(self.mock_agent.agent_context.context_rule.optimization_config.enabled)
        self.assertTrue(self.mock_agent.agent_context.context_rule.llm_compression_config.enabled)
        self.mock_agent.update_context_rule(origin_rule)

    def test_state_management_and_recovery(self):
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
        self.assertIsNotNone(response.answer)

        # Verify state changes after execution
        self.assertTrue(custom_agent.context.state.get('policy_executed', True))
        self.assertTrue(second_agent.agent_context.state.get('policy_executed', True))

class TestHookSystem(TestContextManagement):
    """Test cases for Hook System functionality"""

    def __init__(self):
        super().__init__()
    
    def test_hook_registration(self):
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
        response = self.run_agent(input= """What is an agent. describe within 20 words""")
        self.assertIsNotNone(response.answer)


if __name__ == '__main__':
    testContextManagement = TestContextManagement(config_type="1")
    testContextManagement.test_default_context_configuration()
    testContextManagement = TestContextManagement(config_type="2")
    testContextManagement.test_default_context_configuration()
    testContextManagement = TestContextManagement()
    testContextManagement.test_custom_context_configuration()
    testContextManagement = TestContextManagement()
    testContextManagement.test_state_management_and_recovery()
    testHookSystem = TestHookSystem()
    testHookSystem.test_hook_registration()
    testHookSystem.test_hook_execution()

    