from collections import Counter
from aworld.evaluations.base import Scorer, EvaluationResultRow
from typing import Any, Optional
from aworld.utils.import_package import import_package

import_package('scipy')


class LabelDistributionScorer(Scorer):
    def __init__(self, name: str = None, dataset_column: str = None):
        super().__init__(name)
        self.dataset_column = dataset_column

    async def score(self, index: int, input: dict, output: dict) -> Any:
        """score the execute result.

        Returns:
            score
        """
        return {}

    def summarize(self, result_rows: list[EvaluationResultRow]) -> Optional[dict]:
        '''
            summarize the score rows.
        '''
        from scipy import stats

        column_values = [result.input[self.dataset_column] for result in result_rows]
        c = Counter(column_values)
        label_distribution = {"labels": [k for k in c.keys()], "fractions": [f / len(column_values) for f in c.values()]}
        if isinstance(column_values[0], str):
            label2id = {label: id for id, label in enumerate(label_distribution["labels"])}
            column_values = [label2id[d] for d in column_values]
        skew = stats.skew(column_values)
        return {"label_distribution": label_distribution, "label_skew": skew}
