import unittest
import os
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.core.agent.swarm import Swarm
from aworld.evaluations.eval_targets.agent_eval import AworldTaskEvalTarget
from aworld.evaluations.base import EvalDataCase
from aworld.core.task import Task
from aworld.config import TaskConfig
from aworld.config.conf import EvaluationConfig, DataLoaderConfig
from aworld.dataset.sampler import RangeSampler
from aworld.runners.evaluate_runner import EvaluateRunner

from dotenv import load_dotenv


class EvalPassAtKTest(unittest.IsolatedAsyncioTestCase):

    class TestAgentEvalTarget(AworldTaskEvalTarget):
        agent = Agent(
            conf=AgentConfig(
                llm_provider=os.getenv("LLM_PROVIDER"),
                llm_model_name=os.getenv("LLM_MODEL_NAME"),
                llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
                llm_base_url=os.getenv("LLM_BASE_URL"),
                llm_api_key=os.getenv("LLM_API_KEY"),),
            name="test_agent",
            system_prompt="You are a mathematical calculation agent.",
            agent_prompt="Please provide the calculation results directly without any other explanatory text. Here are the content: {task}"
        )

        async def build_task(self, index: int, input: EvalDataCase[dict]) -> Task:
            return Task(
                id=f"{input.eval_case_id}_{index}",
                user_id=f"{input.eval_case_id}_{index}",
                input=input.case_data["query"],
                swarm=Swarm(self.agent),
                conf=TaskConfig(
                    stream=False,
                    exit_on_failure=True
                )
            )

    async def test_agent_evaluation(self):
        load_dotenv()

        results = await EvaluateRunner(config=EvaluationConfig(
            eval_target=self.TestAgentEvalTarget(),
            eval_criterias=[
                {
                    "metric_name": "answer_accuracy",
                    "threshold": 0.5,
                }
            ],
            eval_dataset_id_or_file_path="tests/evaluations/test_data.csv",
            eval_dataset_load_config=DataLoaderConfig(sampler=RangeSampler(start_index=1, end_index=4)),
            repeat_times=5,
            skip_passed_cases=True,
        )).run()
        print(results)
