# coding: utf-8
# Copyright (c) inclusionAI.
from typing import Optional
from aworld.evaluations.base import EvalDataCase, EvalCaseDataType, MetricResult
from aworld.evaluations.scorers import scorer_register
from aworld.evaluations.scorers.base_validator import LLMAsJudgeScorer
from aworld.evaluations.types import MetricNames


@scorer_register(MetricNames.OUTPUT_RELEVANCE)
class AnswerAccuracyLLMScorer(LLMAsJudgeScorer):

    def _build_judge_system_prompt(self) -> str:
        return """
        Please based on the correct answer given below, determine whether the answer to the original question is correct.

        # Scoring Rubric
        explanation: Explain why the final answer is correct or incorrect based on the correct explanation. Focus only on whether there are substantial differences between the final answer and the correct answer, do not comment on the background of the question, do not attempt to solve it again, do not defend any answers that are different from the correct answer, and only focus on judging whether the answers are consistent.

        score: If the final answer is consistent with the correct answer given above, or within an acceptable small margin of error in numerical questions, then fill in '1'; Otherwise (i.e. any inconsistency, ambiguity, non equivalence, or incorrect extracted answers), fill in '0'.

        Please output in the following standard JSON format without any additional explanatory text:{{"score":1, "explanation":"explain why the final answer is correct or incorrect."}}
        """

    def build_judge_data(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> str:
        question_column = self.eval_config.eval_dataset_query_column or 'question'
        correct_answer_column = self.eval_config.eval_dataset_answer_column or 'answer'
        response_column = self.eval_config.eval_output_answer_column or 'answer'
        return f"""
        [Question]: {input.case_data.get(question_column, '')}
        [Correct_Answer]: {input.case_data.get(correct_answer_column, '')}
        [Response]: {output.get(response_column, '')}
        """

    def convert_judge_response_to_score(self, judge_response: str) -> Optional[dict[str, MetricResult]]:
        json_output = self.fetch_json_from_result(judge_response)
        if json_output:
            return {
                MetricNames.OUTPUT_RELEVANCE: MetricResult(
                    value=json_output.get('score', 0),
                    explanation=json_output.get('explanation', '')
                )
            }
        return None
