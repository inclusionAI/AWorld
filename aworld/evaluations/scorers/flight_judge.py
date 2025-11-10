import json

from aworld.evaluations.base import EvalDataCase, EvalCaseDataType, MetricResult
from typing import Optional
from aworld.evaluations.scorers.metrics import MetricNames
from aworld.evaluations.scorers.scorer_registry import scorer_register
from aworld.evaluations.scorers.llm_as_judge import LLMAsJudgeScorer
import base64
import os
import glob

def encode_image(imag_dir):
    # if image_content is a path to an image file, check type of the image_content to verify
    if isinstance(imag_dir, str):
        with open(imag_dir, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")
    else:
        return base64.b64encode(imag_dir).decode("utf-8")

def get_latest_file_os(directory='.'):
    # glob.glob 获取所有路径，然后筛选出文件，再用 max 找到最新的
    files = (p for p in glob.glob(os.path.join(directory, '*')) if os.path.isfile(p))
    return max(files, key=os.path.getmtime, default=None)

@scorer_register(MetricNames.FLIGHT_JUDGE)
class FlightJudgeLLMScorer(LLMAsJudgeScorer):

    def build_judge_prompt(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> str:
        screenshot_dir  = "./logs/screen_shot/" + input.run_id + "_task#1"
        latest_screenshot = get_latest_file_os(screenshot_dir)
        image_base64 = encode_image(latest_screenshot)

        judge_prompt = json.dumps(
            [
                {
                    "type": "text",
                    "text": """[Task Description]
Your role is to act as an AI Agent Evaluator. Based on the user's query, the agent's execution path, and the final browser screenshot provided, you must determine if the agent's final answer successfully resolves the user's query.

[Evaluation Criteria]

1. Accuracy and Completeness:
The final answer must directly and accurately address the user's question.
It must fulfill all explicit and implicit requirements mentioned in the query (e.g., location, date, direct flights, layovers, airline preferences, departure/arrival times, etc.).

2. Factual Grounding:
The final answer must be strictly grounded in the information visible in the final browser screenshot and be logically consistent with the agent's execution path.
No fabricated or hallucinated information is allowed. Every piece of data in the answer (e.g., prices, times, flight numbers) must be verifiable from the provided evidence.

[Output Format]

Score:
If the final answer meets both of the above criteria, the score is 1.
If either criterion is not met, the score is 0.

Explanation:
You must provide a explanation for your score.
For a score of 1, briefly explain how both criteria were met.
For a score of 0, you must clearly state which criterion was violated and provide a specific example of the failure. 

Please output in the following standard JSON format without any additional explanatory text:
{{"score":0/1, "explanation":"explain why the final answer is correct or incorrect."}}

Here is the task: {task}
"""
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64," + image_base64
                    }
                }
            ]
        )

        return judge_prompt

    def build_judge_data(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> str:
        question_column = self.eval_config.eval_dataset_query_column or 'question'
        response_column = self.eval_config.eval_output_answer_column or 'answer'
        trajectory_column = 'trajectory'
        return f"""
        [Question]: {input.case_data.get(question_column, '')}
        [Trajectory]: {output.get(trajectory_column, '')}
        [Final Answer]: {output.get(response_column, '')}
        """

    def convert_judge_response_to_score(self, judge_response: str) -> Optional[dict[str, MetricResult]]:
        json_output = self.fetch_json_from_result(judge_response)
        if json_output:
            return {
                MetricNames.ANSWER_ACCURACY: MetricResult(
                    value=json_output.get('score', 0),
                    explanation=json_output.get('explanation', '')
                )
            }
        return None
