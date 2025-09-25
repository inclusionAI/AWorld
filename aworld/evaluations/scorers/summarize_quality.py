from aworld.evaluations.base import EvalDataCase, EvalCaseDataType, MetricResult
from typing import Optional
from aworld.evaluations.scorers.metrics import MetricNames
from aworld.evaluations.scorers.scorer_registry import scorer_register
from aworld.evaluations.scorers.llm_as_judge import LLMAsJudgeScorer


SUMMARIZE_QUALITY_EVAL_PROMPT = """
Given an <input> and a <summary>, evaluate the quality of the <summary>.

# Considerations
- Does the <summary> contain the key information in the <input>?
- Is the <summary> concise and informative?
- Is the <summary> grammatically correct?
- Does the <summary> contain information or assertions that are not present in the <input>?

# Scoring Rubric
`excellent`: The <summary> contains all of the key information and entities in the <input>, is concise and informative, is grammatically correct and doesn't contain any information or assertions that are not present in the <input>.

`ok`: The <summary> contains most of the key information and entities in the <input>, is somewhat concise and informative, is mostly grammatically correct and doesn't contain any information or assertions that are not present in the <input>.

`poor`: The <summary> misses most or all of the key information in the <input>, or is very verbose or vague, or is not concise or informative, or has many grammatical errors, or contains information or assertions that are not present in the <input>.

Here is the <input> and <summary> to evaluate:{task}

# Output Format:
{{"quality":"ok", "score_reasoning":"Think step-by-step about the quality of the summary before deciding on the summarization score."}}

"""

TASK_TEMPLATE = """
<input>
{input}
</input>

<summary>
{summary}
</summary>
"""

summarize_quality_score_mapping = {"poor": 0.0, "ok": 0.5, "excellent": 1.0}


@scorer_register(MetricNames.SUMMARIZE_QUALITY)
class SummarizeQualityScorer(LLMAsJudgeScorer):
    """Scorer that uses an LLM agent as a judge to evaluate the quality of the response.

    Args:
        model_config (ModelConfig): Model configuration.
    """

    def build_judge_prompt(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> str:
        return SUMMARIZE_QUALITY_EVAL_PROMPT

    def build_judge_data(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> str:
        query_column = self.eval_config.eval_dataset_query_column or "query"
        output_answer_column = self.eval_config.eval_output_answer_column or "answer"
        return TASK_TEMPLATE.format(input=input.case_data[query_column], summary=output.get(output_answer_column, ''))

    def convert_judge_response_to_score(self, judge_response: str) -> Optional[dict[str, MetricResult]]:
        jsonObj = self.fetch_json_from_result(judge_response)
        if jsonObj:
            return {MetricNames.SUMMARIZE_QUALITY: {"value": summarize_quality_score_mapping[jsonObj["quality"]], "score_reasoning": jsonObj["score_reasoning"]}}
