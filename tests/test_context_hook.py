
# Add the project root to Python path
from pathlib import Path
import sys

from aworld.logs.util import Color, color_log


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

@HookFactory.register(name="CheckContextPreLLMHook", desc="Test pre-LLM hook")
class CheckContextPreLLMHook(PreLLMCallHook):
    def name(self):
        return convert_to_snake("CheckContextPreLLMHook")
    async def exec(self, message: Message, context: Context = None) -> Message:
        assert context.state.get("task") == "What is an agent."
        color_log(f"CheckContextPreLLMHook test state: {context.state}", color=Color.green)
        return message
