import unittest
import os
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.core.context.base import Context
from aworld.core.task import Task
from aworld.config import TaskConfig, TaskRunMode
from aworld.runner import Runners
from aworld.trace.server import get_trace_server
import aworld.trace as trace
from build.lib.aworld.output import observer
import examples.common.tools
from aworld.logs.util import logger

from dotenv import load_dotenv

trace.configure(trace.ObservabilityConfig(trace_server_enabled=True))


class SingleStepTest(unittest.IsolatedAsyncioTestCase):

    async def init_task(self):
        load_dotenv()
        agent = Agent(
            conf=AgentConfig(
                llm_provider=os.getenv("LLM_PROVIDER"),
                llm_model_name=os.getenv("LLM_MODEL_NAME"),
                llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
                llm_base_url=os.getenv("LLM_BASE_URL"),
                llm_api_key=os.getenv("LLM_API_KEY"),),
            name="test_agent",
            system_prompt="You are a helpful assistant. You can user search tool to search baidu and answer the question.",
            agent_prompt="Here are the content: {task}",
            tool_names=["search_api"]
        )

        context = Context()
        task_id = "test_task"

        return Task(
            id=task_id,
            user_id="test_user",
            input="How many men are there in the capital of France?",
            agent=agent,
            conf=TaskConfig(
                stream=False,
                resp_carry_context=True,
                run_mode=TaskRunMode.INTERACTIVAE
            ),
            context=context
        )

    async def test_single_step(self):
        # step 1
        step = 1
        task = await self.init_task()
        responses = await Runners.run_task(task)
        resp = responses[task.id]
        logger.info(f"step {step} resp: {resp}")

        while resp.status == "running":
            step += 1
            task.observation = resp.answer
            responses = await Runners.run_task(task)
            resp = responses[task.id]
            logger.info(f"step {step} resp: {resp.answer}")

        # get_trace_server().join()

    async def test_single_step_api(self):
        '''
            python -m pytest tests/train/test_single_step.py::SingleStepTest::test_single_step_api -v
        '''
        # step 1
        step = 1
        task = await self.init_task()
        is_finished, observation, response = await Runners.step(task)
        logger.info(f"step {step} observation: {observation}")

        while not is_finished:
            step += 1
            is_finished, observation, response = await Runners.step(task)
            logger.info(f"step {step} observation: {observation}")

        # get_trace_server().join()
