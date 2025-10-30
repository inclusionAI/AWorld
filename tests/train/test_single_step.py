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
import examples.common.tools


from dotenv import load_dotenv

trace.configure(trace.ObservabilityConfig(trace_server_enabled=True))


class SingleStepTest(unittest.IsolatedAsyncioTestCase):

    async def test_single_step(self):
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

        # step 1
        step = 1
        task = Task(
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
        responses = await Runners.run_task(task)
        resp = responses[task_id]
        print(f"step {step} resp: {resp}")

        while resp.status == "running":
            step += 1
            task.observation = resp.answer
            responses = await Runners.run_task(task)
            resp = responses[task_id]
            print(f"step {step} resp: {resp.answer}")

        # get_trace_server().join()
