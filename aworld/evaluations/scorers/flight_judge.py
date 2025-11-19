import json

from aworld.core.context.amni import TaskInput
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
    if imag_dir is None:
        raise ValueError("Image path is None, cannot encode image")
    if isinstance(imag_dir, str):
        with open(imag_dir, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")
    else:
        return base64.b64encode(imag_dir).decode("utf-8")

def get_latest_file_os(directory='.'):
    # Use glob.glob to get all paths, filter out files, then use max to find the latest one
    files = (p for p in glob.glob(os.path.join(directory, '*')) if os.path.isfile(p))
    return max(files, key=os.path.getmtime, default=None)

@scorer_register(MetricNames.FLIGHT_JUDGE)
class FlightJudgeLLMScorer(LLMAsJudgeScorer):

    def build_pic_data(self, input: EvalDataCase[EvalCaseDataType]):
        task_prompt = """[Task Description]
Your role is to act as an AI Agent Evaluator. Based on the user's query, the agent's execution path, and the final browser screenshot provided, you must determine if the agent's final answer successfully resolves the user's query.

[Evaluation Criteria]
1. Accuracy:
The final answer must directly and accurately address the user's question.
It must fulfill all explicit and implicit requirements mentioned in the query (e.g., location, date, direct flights, layovers, airline preferences, departure/arrival times, etc.).

2. Factual Grounding:
The final answer must be strictly grounded in the information visible in the final browser screenshot and be logically consistent with the agent's execution path.
No fabricated or hallucinated information is allowed. Every piece of data in the answer (e.g., prices, times, flight numbers) must be verifiable from the provided evidence.

3. Execution Integrity:
The agent successfully retrieved the flight information by navigating the process unimpeded by anti-scraping measures, such as CAPTCHAs or login walls.

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
        task_prompt = """[Task Description]
Based on the answer, execution flow, and final browser screenshot, determine whether the flight query execution process encountered connection issues or anti-scraping mechanisms, including web pages that cannot be opened, user login verification, slider verification, etc.
Note: Only issues that affect the flight query process, making it impossible to obtain final flight information or preventing flight information from loading, should be considered. If pop-up prompts appear but do not affect information retrieval, they should not be counted.
Only when no anti-scraping mechanisms are encountered at every step of the execution process can it be concluded that the above problems were not encountered.

[Output Format]
score: score of 0 means the above problems were not encountered, score of 1 means the above problems were encountered.
explanation: If the above problems were encountered, the specific problem encountered must be explained; if the above problems were not encountered, leave it empty.
Output in JSON format.
Examples:
{{"score":1, "explanation":"User login verification"}}
{{"score":0, "explanation":""}}

[Start Task]
{task}
"""

        screenshot_dir = "./logs/screen_shot/" + input.run_id + "_task#" + input.case_data['id']
        latest_screenshot = get_latest_file_os(screenshot_dir)
        if latest_screenshot is None:
            return [
                {
                    "type": "text",
                    "text": task_prompt
                }
            ]
        
        image_base64 = encode_image(latest_screenshot)

        return [
            {
                "type": "text",
                "text": task_prompt
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64," + image_base64
                }
            }
        ]

    def build_judge_prompt(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> str:
        return ""

    def build_judge_data(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> [str, TaskInput]:
        question_column = self.eval_config.eval_dataset_query_column or 'question'
        response_column = self.eval_config.eval_output_answer_column or 'answer'
        if not output or 'trajectory' not in output:
            return None
        trajectory_list = [msg for key, msg in sorted(output.get('trajectory', {}).items())]

        last_summary_idx = next(
            (i for i in range(len(trajectory_list) - 1, -1, -1) if trajectory_list[i].get('memory_type') == 'summary'), -1
        )

        if last_summary_idx != -1:
            messages_to_process = trajectory_list[:2] + trajectory_list[last_summary_idx:]
        else:
            messages_to_process = trajectory_list

        new_trajectory = [
            {"role": message["role"], "content": message["content"]}
            for message in messages_to_process
        ]
        new_trajectory_str = json.dumps(new_trajectory, ensure_ascii=False)

        # judge_data = f"""
        # [Question]: {input.case_data.get(question_column, '')}
        # [Trajectory]: {new_trajectory_str}
        # [Final Answer]: {output.get(response_column, '')}
        # """
        judge_data = f"""
        [Question]: {input.case_data.get(question_column, '')}
        [Execution Flow]: {new_trajectory_str}
        [Answer]: {output.get(response_column, '')}
        """
        pic_data = self.build_pic_data(input)
        pic_data[0]['text'] = pic_data[0]['text'].format(task=judge_data)
        return pic_data

    def convert_judge_response_to_score(self, judge_response: str) -> Optional[dict[str, MetricResult]]:
        json_output = self.fetch_json_from_result(judge_response)
        if json_output:
            return {
                MetricNames.FLIGHT_JUDGE: MetricResult(
                    value=json_output.get('score', 0),
                    explanation=json_output.get('explanation', '')
                )
            }
        return None
