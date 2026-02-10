# coding: utf-8
# Copyright (c) inclusionAI.
from typing import Dict, Any

from aworld.config import EvaluationConfig
from aworld.evaluations.base import EvalTarget, EvalDataCase


class DelegateEvalTarget(EvalTarget):
    """Target use the constructed `output` as the inference result, mainly to avoid secondary prediction."""

    def __init__(self, output: Dict[str, Any], eval_config: EvaluationConfig = None):
        super().__init__(eval_config=eval_config)
        self.output = output

    async def predict(self, index: int, input: EvalDataCase) -> dict:
        return self.output
