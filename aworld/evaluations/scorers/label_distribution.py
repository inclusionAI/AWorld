from collections import Counter
from aworld.evaluations.base import Scorer, ScorerResult, EvalDataCase, EvalCaseResult
from typing import Optional
from aworld.evaluations.scorers.scorer_registry import scorer_register
from aworld.evaluations.scorers.metrics import MetricNames

from aworld.utils.import_package import import_package

import_package('scipy')


@scorer_register(MetricNames.LABEL_DISTRIBUTION)
class LabelDistributionScorer(Scorer[dict]):

    def __init__(self, name: str = None, dataset_column: str = None):
        super().__init__(name)
        self.dataset_column = dataset_column

    async def score(self, index: int, input: EvalDataCase[dict], output: dict) -> ScorerResult:
        """score the execute result.

        Returns:
            score
        """
        return ScorerResult(scorer_name=self.name, metric_results={MetricNames.LABEL_DISTRIBUTION: {"value": 0.0}})

    def summarize(self, result_rows: list[EvalCaseResult], repeat_times: int) -> Optional[dict]:
        '''
            summarize the score rows.
        '''
        from scipy import stats

        column_values = [result.input.case_data[self.dataset_column] for result in result_rows]
        c = Counter(column_values)
        label_distribution = {"labels": [k for k in c.keys()], "fractions": [f / len(column_values) for f in c.values()]}
        if isinstance(column_values[0], str):
            label2id = {label: id for id, label in enumerate(label_distribution["labels"])}
            column_values = [label2id[d] for d in column_values]
        skew = stats.skew(column_values)
        return {MetricNames.LABEL_DISTRIBUTION: label_distribution, "label_skew": skew}
