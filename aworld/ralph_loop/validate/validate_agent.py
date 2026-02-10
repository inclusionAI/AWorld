# coding: utf-8
# Copyright (c) inclusionAI.
from typing import Dict, Any, List

from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.evaluations.base import Evaluator, EvalDataCase, EvalDataset
from aworld.ralph_loop.validate.target import DelegateEvalTarget


class ValidateAgent(Agent):
    def __init__(self, evaluator: Evaluator, **kwargs):
        super().__init__(**kwargs)
        self.evaluator = evaluator

    async def async_policy(self,
                           observation: Observation,
                           info: Dict[str, Any] = {},
                           message: Message = None,
                           **kwargs) -> List[ActionModel]:
        case = EvalDataCase(
            case_data={
                "format_type": "text",
                "user_input": info.get('user_input'),
                "ground_truth": info.get('answer'),
            }
        )
        dataset = EvalDataset(eval_cases=[case])
        eval_target = DelegateEvalTarget(output=observation.content)
        result = await self.evaluator.evaluate(dataset=dataset, eval_target=eval_target)
        case_result = result.eval_case_results[0]
        passed = all(
            sr.metric_results[k]["eval_status"].value == 1
            for k, sr in case_result.score_rows.items()
        )
        scores = {
            m: mr["value"]
            for _, sr in case_result.score_rows.items()
            for m, mr in sr.metric_results.items()
        }
        action = ActionModel(agent_name=self.id(), policy_info={
            "passed": passed,
            "scores": scores,
            "details": case_result.score_rows,
            "reason": "Validation failed" if not passed else "Validation passed",
        })
        return [action]
