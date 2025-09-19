from aworld.evaluations.base import Scorer
from typing import Dict, Any


class AnswerAccuracyScorer(Scorer):

    def __init__(self, query_column: str = 'query', answer_column: str = 'answer'):
        super().__init__()
        self.query_column = query_column
        self.answer_column = answer_column

    async def score(self, index: int, input: dict, output: dict) -> float:
        answer_except = input[self.answer_column]
        answer_actual = output.get(self.answer_column, '')
        return 1.0 if answer_except.strip() == answer_actual.strip() else 0.0
