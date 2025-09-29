from aworld.evaluations.base import EvalDataCase, EvalCaseDataType, Scorer, ScorerResult
from aworld.evaluations.scorers.metrics import MetricNames
from aworld.evaluations.scorers.scorer_registry import scorer_register


@scorer_register(MetricNames.PREDICT_TIME_COST_MS)
class TimeCostScorer(Scorer[EvalCaseDataType]):
    '''
    Scorer for measuring the time cost of predict method.
    '''

    def __init__(self, name: str = "TimeCostScorer"):
        super().__init__(name)

    async def score(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> ScorerResult:
        '''
        Calculate the time cost of predict method.
        '''
        scorer_result = ScorerResult(scorer_name=self.name)
        time_cost_ms = output.get('_time_cost_ms', 0.0)

        scorer_result.metric_results = {
            MetricNames.PREDICT_TIME_COST_MS: {
                "value": time_cost_ms
            }
        }

        return scorer_result
