import unittest
from aworld.evaluations.sorers.label_distribution import LabelDistributionScorer
from aworld.evaluations.base import Dataset, Evaluator


class DatesetEvaluationTest(unittest.IsolatedAsyncioTestCase):

    async def test_label_distribution(self):

        data = [{"label": "a"}, {"label": "b"}, {"label": "c"}, {"label": "a"}]
        dataset = Dataset(rows=data)

        evaluator = Evaluator(scorers=[LabelDistributionScorer(dataset_column="label")])
        result = await evaluator.evaluate(dataset)
        print(f"result: {result}")
        self.assertEqual(result.summary["LabelDistributionScorer"]["label_distribution"], {"labels": ["a", "b", "c"], "fractions": [0.5, 0.25, 0.25]})
