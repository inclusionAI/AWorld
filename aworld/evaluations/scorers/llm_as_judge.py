# coding: utf-8
# Copyright (c) inclusionAI.
import abc
import os
import json
import re
from typing import Optional, Generic

from aworld.core.context.base import Context
from aworld.evaluations.base import MetricResult, Scorer, EvalDataCase, EvalCaseDataType, ScorerResult
from aworld.agents.llm_agent import Agent
from aworld.config.conf import ModelConfig, AgentConfig, EvaluationConfig
from aworld.logs.util import logger
from aworld.utils.run_util import exec_agent


class LLMAsJudgeScorer(Scorer, Generic[EvalCaseDataType]):
    """Scorer that uses an LLM agent as a judge to evaluate the quality of the response."""

    def __init__(self,
                 name: str = None,
                 eval_config: EvaluationConfig = None,
                 model_config: ModelConfig = None,
                 system_prompt: str = None):
        super().__init__(name=name, eval_config=eval_config)

        self.model_config = model_config or ModelConfig(
            llm_provider=os.getenv('LLM_PROVIDER', 'openai'),
            llm_model_name=os.getenv('LLM_MODEL_NAME'),
            llm_temperature=float(os.getenv('LLM_TEMPERATURE', 0.3)),
            llm_base_url=os.getenv('LLM_BASE_URL', None),
            llm_api_key=os.getenv('LLM_API_KEY', None),
        )
        self.agent_config = AgentConfig(
            llm_provider=self.model_config.llm_provider,
            llm_model_name=self.model_config.llm_model_name,
            llm_temperature=self.model_config.llm_temperature,
            llm_base_url=self.model_config.llm_base_url,
            llm_api_key=self.model_config.llm_api_key,
        )
        self.system_prompt = system_prompt

    @abc.abstractmethod
    def build_judge_data(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> str:
        """Builds the input for the judge agent task.

        Args:
            index: The index of the evaluation example within the dataset.
            input: The example data,.
            output: The agent's generated output/response to be judged.

        Returns:
            str: The input string for the judge agent task.

        Example:
            [Question]: {input.case_data.get('question', '')}
            [Correct_Answer]: {input.case_data.get('answer', '')}
            [Response]: {output.get('answer', '')}
        """
        raise NotImplementedError("build_judge_data must be implemented in subclasses")

    @abc.abstractmethod
    def convert_judge_response_to_score(self, judge_response: str) -> Optional[dict[str, MetricResult]]:
        """Convert judge response to score.

        Args:
            judge_response: Judge response string.

            Optional[dict[str, MetricResult]]: Dict of metric results if conversion is successful, None otherwise.
            The key is metric name, value is metric result.
        """
        raise NotImplementedError("convert_judge_response_to_score must be implemented in subclasses")

    async def score(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> ScorerResult:
        """Score the output using the judge agent.

        Args:
            index: The index of the evaluation example within the dataset.
            input: The example data,.
            output: The agent's generated output/response to be judged.

        Returns:
            ScorerResult: Scorer result.
        """
        score_agent = Agent(conf=self.agent_config,
                            name='score_agent',
                            system_prompt=self._build_judge_system_prompt())

        task_input = self.build_judge_data(index=index, input=input, output=output)
        response = await exec_agent(task_input, agent=score_agent, context=Context())
        metric_results = self.convert_judge_response_to_score(response.answer)
        if metric_results:
            return ScorerResult(scorer_name=self.name, metric_results=metric_results)
        return ScorerResult(scorer_name=self.name, metric_results={})

    def fetch_json_from_result(self, input_str) -> dict:
        json_match = re.search(r'\{[^{}]*\}', input_str, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            try:
                return json.loads(json_str)
            except json.JSONDecodeError as e:
                logger.warning(f"_fetch_json_from_result json_str: {json_str} error: {e}")
        return {}

    def _build_judge_system_prompt(self) -> str:
        """Get system prompt for judge model.

        Returns:
            str: System prompt.
        """
        return self.system_prompt
