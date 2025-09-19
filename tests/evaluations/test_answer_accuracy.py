
from aworld.evaluations.base import Dataset, Evaluator, Evaluatable
from aworld.evaluations.sorers.answer_accuracy import AnswerAccuracyScorer
import unittest
import os
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, ModelConfig
from aworld.evaluations.evaluatables.agent_avaluatable import AgentEvaluatable
from aworld.evaluations.dataset_util import load_dataset_from_csv
from dotenv import load_dotenv


class AnswerAccuracyEvaluationTest(unittest.IsolatedAsyncioTestCase):

    async def test_load_dataset_from_csv(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        dataset = load_dataset_from_csv(os.path.join(current_dir, 'test_data.csv'))
        print(dataset.rows)
        self.assertEqual(len(dataset.rows), 2)

    async def test_summarize_quality(self):
        load_dotenv()

        # dataset = Dataset(
        #     rows=[
        #         {
        #             "query": "1+1=",
        #             "answer": "2"
        #         }, {
        #             "query": "4x5=",
        #             "answer": "20"
        #         }
        #     ]
        # )

        current_dir = os.path.dirname(os.path.abspath(__file__))
        dataset = load_dataset_from_csv(os.path.join(current_dir, 'test_data.csv'))

        agent_config = AgentConfig(
            llm_provider=os.getenv("LLM_PROVIDER"),
            llm_model_name=os.getenv("LLM_MODEL_NAME"),
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
            llm_base_url=os.getenv("LLM_BASE_URL"),
            llm_api_key=os.getenv("LLM_API_KEY"),
        )
        agent = Agent(
            conf=agent_config,
            name="agent_for_eval",
            system_prompt="You are a mathematical calculation agent.",
            agent_prompt="Please provide the calculation results directly without any other explanatory text. Here are the content: {task}"
        )
        evaluator = Evaluator(scorers=[AnswerAccuracyScorer()])
        results = await evaluator.evaluate(dataset=dataset, evaluatable=AgentEvaluatable(agent=agent))
        print(results)
