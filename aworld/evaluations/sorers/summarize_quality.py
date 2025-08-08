from aworld.core import task
from aworld.evaluations.base import Scorer
from aworld.config.conf import ModelConfig, AgentConfig
from typing import Any
from aworld.agents.llm_agent import Agent
from aworld.runner import Runners
from aworld.logs.util import logger
import json
import re

DEFAULT_SUMMARIZE_QUALITY_SYSTEM_PROMPT = """
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
"""

TASK_TEMPLATE = """
<input>
{input}
</input>

<summary>
{summary}
</summary>
"""

DEFAULT_SUMMARIZE_QUALITY_USER_PROMPT = """
Evaluate the quality of the following <summary> given the <input>:
{task}

Please output in the following standard JSON format without any additional explanatory text:
{{"quality":"ok", "score_reasoning":"Think step-by-step about the quality of the summary before deciding on the summarization score."}}
"""

summarize_quality_score_mapping = {"poor": 0.0, "ok": 0.5, "excellent": 1.0}


class SummarizeQualityScorer(Scorer):

    def __init__(self, model_config: ModelConfig, query_column: str = 'query', answer_column: str = 'answer'):
        super().__init__()
        self.model_config = model_config
        self.query_column = query_column
        self.answer_column = answer_column
        self.agent_config = AgentConfig(
            llm_provider=model_config.llm_provider,
            llm_model_name=model_config.llm_model_name,
            llm_temperature=model_config.llm_temperature,
            llm_base_url=model_config.llm_base_url,
            llm_api_key=model_config.llm_api_key,
        )

    def _fetch_json_from_result(self, input_str):
        json_match = re.search(r'\{[^{}]*\}', input_str, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            try:
                return json.loads(json_str)
            except json.JSONDecodeError as e:
                logger.warning(f"_fetch_json_from_result json_str: {json_str} error: {e}")
        return ""

    async def score(self, index: int, input: dict, output: dict) -> Any:
        """Score the quality of the summary.

        Args:
            index: The index of the input.
            input: The input dict.
            output: The output dict.

        Returns:
            The score of the summary.
        """
        query = input[self.query_column]
        answer = output[self.answer_column]
        task_input = TASK_TEMPLATE.format(input=query, summary=answer)

        score_agent = Agent(conf=self.agent_config, name='score_agent',
                            system_prompt=DEFAULT_SUMMARIZE_QUALITY_SYSTEM_PROMPT,
                            agent_prompt=DEFAULT_SUMMARIZE_QUALITY_USER_PROMPT)

        response = await Runners.run(task_input, agent=score_agent)
        jsonObj = self._fetch_json_from_result(response.answer)
        if jsonObj:
            return {"summary_quality_score": summarize_quality_score_mapping[jsonObj["quality"]], "score_reasoning": jsonObj["score_reasoning"]}
        return {"summary_quality_score": 0.0, "score_reasoning": "score response error"}
