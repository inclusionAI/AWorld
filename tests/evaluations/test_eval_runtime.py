import unittest
from aworld.evaluations.evel_runtime.local_eval_runtime import LocalEvaluateRunner
from aworld.evaluations.base import EvalRunConfig

from dotenv import load_dotenv


class EvalRuntimeTest(unittest.IsolatedAsyncioTestCase):

    async def test_agent_evaluation(self):
        load_dotenv()

        eval_config = EvalRunConfig(
            eval_target_full_class_name="aworld.evaluations.eval_targets.agent_eval.AworldAgentEvalTarget",
            eval_target_config={
                "agent_config": {
                    "name": "test_agent",
                    "system_prompt": "You are a mathematical calculation agent.",
                    "agent_prompt": "Please provide the calculation results directly without any other explanatory text. Here are the content: {task}",
                    "conf": {}
                },
                "query_column": "question",
            },
            eval_criterias=[
                {
                    "metric_name": "answer_accuracy",
                    "threshold": 0.5,
                }
            ],
            eval_dataset_id_or_file_path="tests/evaluations/agent_eval_data.jsonl",
        )

        results = await LocalEvaluateRunner().eval_run(eval_config=eval_config)
        print(results)
