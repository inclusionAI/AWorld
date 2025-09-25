import unittest
import os
from aworld.agents.llm_agent import Agent
from aworld.evaluations.scorers.summarize_quality import SummarizeQualityScorer
from aworld.evaluations.eval_targets.agent_eval import AworldAgentEvalTarget
from aworld.evaluations.base import EvalDataset, EvalDataCase, Evaluator
from aworld.config.conf import AgentConfig, ModelConfig
from dotenv import load_dotenv


class AgentEvaluationTest(unittest.IsolatedAsyncioTestCase):

    async def test_summarize_quality(self):
        load_dotenv()
        eval_dataset_id = "test_dataset"
        data = [
            {
                "query": "Several multilingual benchmark datasets have been developed in a semi-automatic manner in the recent past to measure progress and understand the state-of-the-art in the multilingual capabilities of Large Language Models. However, there is not a lot of attention paid to the quality of the datasets themselves, despite the existence of previous work in identifying errors in even fully human-annotated test sets. In this paper, we manually analyze recent multilingual evaluation sets in two languages - French and Telugu, identifying several errors in the process."
            }, {
                "query": "Achieving multilingual fairness in AI systems thatincorporate large language models (LLMs) re-quires not only careful curation of pre-training dataand post-training data, but also (and perhaps moreimportantly) evaluation data, as only the latter canenable us to accurately track progress of these sys-tems on the various tasks they perform."
            }
        ]

        data_cases = [EvalDataCase(eval_dataset_id=eval_dataset_id, case_data=d) for d in data]
        dataset = EvalDataset(eval_dataset_id=eval_dataset_id, eval_cases=data_cases)
        
        summarize_agent_config = AgentConfig(
            llm_provider=os.getenv("LLM_PROVIDER"),
            llm_model_name=os.getenv("LLM_MODEL_NAME"),
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
            llm_base_url=os.getenv("LLM_BASE_URL"),
            llm_api_key=os.getenv("LLM_API_KEY"),
        )
        summarize_agent = Agent(
            conf=summarize_agent_config,
            name="summary_agent",
            system_prompt="You are a helpful general summary agent.",
            agent_prompt="Summarize the following text in one clear and concise paragraph, capturing the key ideas without missing critical points. Ensure the summary is easy to understand and avoids excessive detail. Here are the content: {task}"
        )

        score_llm_config = ModelConfig(
            llm_provider=os.getenv("LLM_PROVIDER"),
            llm_model_name=os.getenv("LLM_MODEL_NAME"),
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
            llm_base_url=os.getenv("LLM_BASE_URL"),
            llm_api_key=os.getenv("LLM_API_KEY"),
        )

        evaluatable = AworldAgentEvalTarget(agent=summarize_agent)
        evaluator = Evaluator(scorers=[SummarizeQualityScorer(model_config=score_llm_config)])

        result = await evaluator.evaluate(dataset, evaluatable)
        print(f"result: {result}")
