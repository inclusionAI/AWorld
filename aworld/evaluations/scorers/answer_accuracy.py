from aworld.evaluations.base import Scorer, ScorerResult, EvalDataCase
from aworld.evaluations.scorers.metrics import MetricNames
from aworld.evaluations.scorers.scorer_registry import scorer_register


@scorer_register(MetricNames.ANSWER_ACCURACY)
class AnswerAccuracyScorer(Scorer):

    def __init__(self, query_column: str = 'query', answer_column: str = 'answer'):
        super().__init__()
        self.query_column = query_column
        self.answer_column = answer_column

    async def score(self, index: int, input: EvalDataCase[dict], output: dict) -> ScorerResult:
        answer_except = input.case_data[self.answer_column]
        answer_actual = output.get(self.answer_column, '')
        value = 1.0 if answer_except.strip() == answer_actual.strip() else 0.0
        return ScorerResult(scorer_name=self.name, metric_results={MetricNames.ANSWER_ACCURACY: {"value": value}})
