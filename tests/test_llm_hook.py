
# Add the project root to Python path
from pathlib import Path
import sys

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

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
from tests.base_test import BaseTest


@HookFactory.register(name="TestPreLLMHook", desc="Test pre-LLM hook")
class TestPreLLMHook(PreLLMCallHook):
    def name(self):
        return convert_to_snake("TestPreLLMHook")
    async def exec(self, message: Message, context: Context = None) -> Message:
        agent = AgentFactory.agent_instance(message.sender)
        agent_context = agent.agent_context
        if agent_context is not None:
            agent_context.step = 1 
        assert agent_context.step == 1 or agent_context.step == 2
        return message

@HookFactory.register(name="TestPostLLMHook", desc="Test post-LLM hook")
class TestPostLLMHook(PostLLMCallHook):
    def name(self):
        return convert_to_snake("TestPostLLMHook")
    async def exec(self, message: Message, context: Context = None) -> Message:
        agent = AgentFactory.agent_instance(message.sender)
        agent_context = agent.agent_context
        if agent_context is not None and agent_context.llm_output is not None:
            # Test dynamic prompt adjustment based on LLM output
            if hasattr(agent_context.llm_output, 'content'):
                content = agent_context.llm_output.content.lower()
                if content is not None:
                    agent_context.agent_prompt = "Success mode activated"
        assert agent_context.agent_prompt == "Success mode activated"
        return message
