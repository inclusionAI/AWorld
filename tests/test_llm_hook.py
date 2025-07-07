
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
        context = agent.context
        context.context_info.set('step', 1)
        return message

@HookFactory.register(name="TestPostLLMHook", desc="Test post-LLM hook")
class TestPostLLMHook(PostLLMCallHook):
    def name(self):
        return convert_to_snake("TestPostLLMHook")
    async def exec(self, message: Message, context: Context = None) -> Message:
        agent = AgentFactory.agent_instance(message.sender)
        context = agent.context
        assert context.context_info.get('step') == 1
        return message
