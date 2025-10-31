import unittest

from dotenv import load_dotenv

from aworld.config.conf import EvaluationConfig

from aworld.runners.evaluate_runner import EvaluateRunner


class EvalRuntimeTest(unittest.IsolatedAsyncioTestCase):

    async def test_agent_evaluation(self):
        load_dotenv()

        eval_config = EvaluationConfig(
            eval_target_full_class_name="aworld.evaluations.eval_targets.agent_eval.AworldAgentEvalTarget",
            eval_target_config={
                "agent_config": {
                    "name": "test_agent",
                    "system_prompt": "You are a mathematical calculation agent.",
                    "conf": {}
                },
            },
            eval_criterias=[
                {
                    "metric_name": "answer_accuracy",
                    "threshold": 0.5,
                }
            ],
            eval_dataset_id_or_file_path="tests/evaluations/agent_eval_data.jsonl",
            eval_dataset_query_column="question",
        )

        results = await EvaluateRunner(config=eval_config).run()
        print(results)
