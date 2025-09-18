import unittest
from aworld.evaluations.scorers.label_distribution import LabelDistributionScorer
from aworld.evaluations.base import EvalDataset, EvalDataCase, Evaluator


class DatesetEvaluationTest(unittest.IsolatedAsyncioTestCase):

    async def test_label_distribution(self):

        data = [{"label": "a"}, {"label": "b"}, {"label": "c"}, {"label": "a"}]
        eval_dataset_id = "test_dataset"

        data_cases = [EvalDataCase(eval_dataset_id=eval_dataset_id, case_data=d) for d in data]

        dataset = EvalDataset(eval_dataset_id=eval_dataset_id, eval_cases=data_cases)

        evaluator = Evaluator(scorers=[LabelDistributionScorer(dataset_column="label")])
        result = await evaluator.evaluate(dataset)
        print(f"result: {result}")
        self.assertEqual(result.summary["LabelDistributionScorer"]["label_distribution"], {"labels": ["a", "b", "c"], "fractions": [0.5, 0.25, 0.25]})
