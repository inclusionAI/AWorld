# coding: utf-8
# Copyright (c) inclusionAI.
import os
import re
from typing import Any, Dict, List

from aworld.config import EvaluationConfig, ModelConfig
from aworld.evaluations.base import Scorer, ScorerResult, EvalStatus, MetricResult, EvalDataCase
from aworld.evaluations.scorers import scorer_register
from aworld.ralph_loop.validate.base_validator import LlmValidator, RuleValidator
from aworld.ralph_loop.validate.types import ValidationMetrics


@scorer_register(ValidationMetrics.OUTPUT_CORRECTNESS)
class OutputCorrectnessScorer(RuleValidator):
    def __init__(self, eval_config: EvaluationConfig = None):
        super().__init__(name=ValidationMetrics.OUTPUT_CORRECTNESS, eval_config=eval_config)

    async def score(self, index: int, input: EvalDataCase, output: Any) -> ScorerResult:
        try:
            ground_truth = input.case_data.get("ground_truth")
            expected_keywords = input.case_data.get("expected_keywords", [])

            answer = self._extract(output, key="answer")

            if not answer:
                return self._failed_result("Empty answer")

            if ground_truth:
                score, reason = self._match_ground_truth(answer, ground_truth)
                if score >= 0.8:
                    return self._success_result(score, f"Output matching: {score:.2f}")
                else:
                    return self._partial_result(score, f"Partial matching: {score:.2f}")

            if expected_keywords:
                score, reason = self._check_keywords(answer, expected_keywords)
                if score >= 0.8:
                    return self._success_result(score, reason)
                else:
                    return self._partial_result(score, reason)

            return self._success_result(1.0, "Unable to verify (missing standard answers or keywords)")

        except Exception as e:
            return self._failed_result(f"Validation failed: {str(e)}")

    def _match_ground_truth(self, answer: str, ground_truth: str) -> tuple[float, str]:
        answer_lower = answer.lower().strip()
        truth_lower = ground_truth.lower().strip()

        if answer_lower == truth_lower:
            return 1.0, "exact match"

        if truth_lower in answer_lower:
            return 0.9, "contains standard answers"

        # Number matching
        answer_numbers = re.findall(r'-?\d+\.?\d*', answer)
        truth_numbers = re.findall(r'-?\d+\.?\d*', ground_truth)

        if answer_numbers and truth_numbers:
            if answer_numbers[0] == truth_numbers[0]:
                return 0.95, "Number matching"

        common_words = set(answer_lower.split()) & set(truth_lower.split())
        if common_words:
            similarity = len(common_words) / max(len(answer_lower.split()), len(truth_lower.split()))
            return similarity, f"simple similarity: {similarity:.2f}"

        return 0.0, "mismatch"

    def _check_keywords(self, answer: str, keywords: List[str]) -> tuple[float, str]:
        answer_lower = answer.lower()
        found_keywords = [kw for kw in keywords if kw.lower() in answer_lower]

        if not keywords:
            return 1.0, "No keywords"

        score = len(found_keywords) / len(keywords)
        missing = set(keywords) - set(found_keywords)

        if score >= 0.8:
            return score, f"contains {len(found_keywords)}/{len(keywords)} keywords"
        else:
            return score, f"missing keywords: {', '.join(list(missing)[:3])}"

    def _success_result(self, score: float, message: str) -> ScorerResult:
        return ScorerResult(
            scorer_name=self.name,
            metric_results={
                "answer_correctness": MetricResult(
                    value=score,
                    eval_status=EvalStatus.PASSED,
                    metadata={"message": message}
                )
            }
        )

    def _partial_result(self, score: float, message: str) -> ScorerResult:
        return ScorerResult(
            scorer_name=self.name,
            metric_results={
                "answer_correctness": MetricResult(
                    value=score,
                    eval_status=EvalStatus.PASSED if score >= 0.5 else EvalStatus.FAILED,
                    metadata={"message": message}
                )
            }
        )

    def _failed_result(self, reason: str) -> ScorerResult:
        return ScorerResult(
            scorer_name=self.name,
            metric_results={
                "answer_correctness": MetricResult(
                    value=0.0,
                    eval_status=EvalStatus.FAILED,
                    metadata={"reason": reason}
                )
            }
        )


@scorer_register(ValidationMetrics.OUTPUT_LENGTH)
class OutputLengthScorer(OutputCorrectnessScorer, RuleValidator):
    def __init__(self, eval_config: EvaluationConfig = None, min_length: int = 10, max_length: int = 1000, **kwargs):
        super().__init__(eval_config=eval_config)
        self.name = ValidationMetrics.OUTPUT_LENGTH
        self.min_length = min_length
        self.max_length = max_length

    async def score(self, index: int, input: Any, output: Any) -> ScorerResult:
        try:
            answer = self._extract(output, key="answer")

            min_len = input.get("min_length", self.min_length)
            max_len = input.get("max_length", self.max_length)

            answer_length = len(answer)

            if answer_length < min_len:
                return self._failed_result(
                    f"answer too short: {answer_length} (less than: {min_len})"
                )
            elif answer_length > max_len:
                return self._failed_result(
                    f"answer too long: {answer_length} (more than: {max_len})"
                )
            else:
                score = 1.0
                if answer_length < min_len * 1.2:
                    score = 0.8
                elif answer_length > max_len * 0.8:
                    score = 0.9

                return self._success_result(
                    score,
                    f"Appropriate length: {answer_length} ({min_len}-{max_len})"
                )
        except Exception as e:
            return self._failed_result(f"verification failed: {str(e)}")


class OutputLlmScore(LlmValidator):
    def build_judge_data(self, index: int, input: Any, output: Any) -> str:
        question = input.get("question", "Unknown")
        context = input.get("context", "")
        answer = output.get("answer", output.get("content", "")) if isinstance(output, dict) else str(output)

        prompt = f"""User Query: {question}

    {f"Context: {context}" if context else ""}

    Model Response: {answer}
    """
        return prompt


@scorer_register(
    ValidationMetrics.OUTPUT_RELEVANCE,
    model_config=ModelConfig(
        llm_provider=os.getenv("VALIDATE_LLM_PROVIDER", os.getenv("LLM_PROVIDER")),
        llm_model_name=os.getenv("VALIDATE_LLM_MODEL_NAME", os.getenv("LLM_MODEL_NAME")),
        llm_temperature=float(os.getenv("VALIDATE_LLM_TEMPERATURE", os.getenv("LLM_TEMPERATURE", "0.7"))),
        llm_base_url=os.getenv("VALIDATE_LLM_BASE_URL", os.getenv("LLM_BASE_URL")),
        llm_api_key=os.getenv("VALIDATE_LLM_API_KEY", os.getenv("LLM_API_KEY")),
    )
)
class OutputRelevanceScorer(OutputLlmScore):
    """Verify the correlation between the answer and the question

    Check:
        The answer address the problem.
        The answer deviate from the topic.
        The answer contain irrelevant content.
    """

    def __init__(self, eval_config: EvaluationConfig = None, model_config: ModelConfig = None):
        super().__init__(name=ValidationMetrics.OUTPUT_RELEVANCE, eval_config=eval_config, model_config=model_config)

    def _build_judge_system_prompt(self) -> str:
        return """# Role
You are an expert in evaluating Search and Question Answering (QA) quality. Your task is to assess the **relevance** of the [Model Response] relative to the [User Query].

# Task
Please carefully read the User Query and the Model Response. Rate the response based on the criteria below and provide a concise reason for your score.

# Evaluation Criteria (1-5 Scale)
- **1.0 (Perfect Relevance)**: The response directly and accurately addresses the user's intent. It contains all necessary information without any superfluous or irrelevant content.
- **0.7-0.9 (High Relevance)**: The response addresses the main intent but may contain minor unnecessary details (fluff) or miss very minor nuances.
- **0.5-0.7 (Partial Relevance)**: The response addresses part of the question but misses key information, or the core answer is buried under significant irrelevant content.
- **0.2-0.4 (Low Relevance)**: The response mentions keywords from the query but fails to address the user's core intent, or answers a different/wrong question.
- **0.0-0.2 (Irrelevant)**: The response is completely unrelated to the user's query, nonsense, or a complete hallucination.

# Guidelines
- **Focus on Intent Alignment**: Determine if the response actually answers *what* the user is asking.
- **Ignore Factuality**: For this specific metric, do not verify external facts (unless obviously nonsensical). Focus on whether the content *attempts* to answer the prompt.
- **Ignore Style**: Do not penalize for tone or formatting issues unless they make the text unreadable.

# Output Format
Please strictly output in the following JSON format:
{
    "score": "<float>, 0-1",
    "reason": "<string>, point out where it is not relevant"
}

# Examples
## Example 1: High Relevance
**Query**: How do I reverse a list in Python?
**Response**: You can use the list.reverse() method or slice notation list[::-1].
**Output**:
{
    "score": 1,
    "reason": "The response directly provides the two core methods to reverse a list, perfectly addressing the user's intent."
}

## Example 2: Low Relevance (Topic Drift)
**Query**: What is the range of the Tesla Model 3?
**Response**: Tesla is a great electric car company. The Model Y is their best-selling SUV and has a lot of space.
**Output**:
{
    "score": 0.2,
    "reason": "The response discusses Tesla and the Model Y, failing to provide the specific range information for the Model 3 requested by the user."
}

## Example 3: Redundant (High Noise)
**Query**: What is the weather in Beijing tomorrow?
**Response**: Weather is very important for human survival. Beijing is the capital of China with a long history. The forecast says it will be 20°C and sunny tomorrow.
**Output**:
{
    "score": 0.8,
    "reason": "The response contains the correct answer regarding the weather, but the score is lowered slightly due to the unnecessary preamble about history and the general importance of weather."
}
"""


@scorer_register(
    ValidationMetrics.OUTPUT_COMPLETENESS,
    model_config=ModelConfig(
        llm_provider=os.getenv("VALIDATE_LLM_PROVIDER", os.getenv("LLM_PROVIDER")),
        llm_model_name=os.getenv("VALIDATE_LLM_MODEL_NAME", os.getenv("LLM_MODEL_NAME")),
        llm_temperature=float(os.getenv("VALIDATE_LLM_TEMPERATURE", os.getenv("LLM_TEMPERATURE", "0.7"))),
        llm_base_url=os.getenv("VALIDATE_LLM_BASE_URL", os.getenv("LLM_BASE_URL")),
        llm_api_key=os.getenv("VALIDATE_LLM_API_KEY", os.getenv("LLM_API_KEY")),
    )
)
class OutputCompletenessScorer(OutputLlmScore):
    """Verify the completeness of the answer

    Check:
        The answer provide a complete answer to the question
        Miss any key information
        Covered all aspects of the problem
    """

    def __init__(self, eval_config: EvaluationConfig = None, model_config: ModelConfig = None):
        super().__init__(name=ValidationMetrics.OUTPUT_COMPLETENESS, eval_config=eval_config, model_config=model_config)

    def _build_judge_system_prompt(self) -> str:
        return """# Role
You are an expert Quality Assurance Analyst for AI systems. Your specific task is to evaluate the **Completeness** of the [Model Response] relative to the [User Query].

# Task
Analyze the User Query to identify all distinct questions, constraints, and information requirements. Then, determine how thoroughly the Model Response addresses these requirements.

# Evaluation Criteria (1-5 Scale)
- **1.0 (Fully Complete)**: The response answers all parts of the query, including sub-questions and constraints. It provides sufficient depth and detail. No aspect of the user's intent is left unaddressed.
- **0.7-0.9 (Mostly Complete)**: The response addresses the main question and most sub-questions but may miss a very minor detail, context, or nuance.
- **0.5-0.7 (Partially Complete)**: The response addresses the primary question but misses a significant sub-question (e.g., asked for "Pros and Cons" but only provided "Pros") or provides a very superficial answer where depth was expected.
- **0.2-0.4 (Incomplete)**: The response addresses a small part of the query but misses the majority of the required information or ignores critical constraints.
- **0.0-0.2 (Deficient)**: The response fails to answer the core question entirely or provides an answer so brief/vague that it is functionally useless.

# Guidelines
- **Check for Multi-part Questions**: If the user asks "Who is X and what did they do?", a complete answer must address *both* "Who" and "What".
- **Check for Constraints**: If the user asks for "3 examples", a response with only 1 example is incomplete.
- **Check for "Don't Know"**: If the model states it cannot answer due to lack of information (and this is true based on the context), rate it based on whether it explained *why* it couldn't answer, rather than just returning an empty string.
- **Distinction from Relevance**: A response can be highly relevant (on topic) but incomplete (missing half the data). Focus only on missing information.

# Output Format
Please strictly output in the following JSON format:
{
    "score": "<float>, 0-1",
    "reason": "<string>, point out the missing information"
}

# Examples
## Example 1: Fully Complete (Score 1.0)
**Query**: What are the capital cities of France, Germany, and Spain?
**Response**: The capital of France is Paris, the capital of Germany is Berlin, and the capital of Spain is Madrid.
**Output**:
{
    "score": 1.0,
    "reason": "The response successfully identifies the capital cities for all three requested countries."
}

## Example 2: Partially Complete (Score 0.6)
**Query**: Explain the benefits and risks of using AI in healthcare.
**Response**: AI in healthcare offers amazing benefits such as faster diagnostics, personalized medicine plans, and operational efficiency in hospitals.
**Output**:
{
    "score": 0.6,
    "reason": "The user explicitly asked for 'benefits and risks'. The model provided a good list of benefits but completely failed to address the 'risks' aspect."
}

## Example 3: Incomplete / Constraint Violation (Score 0.2)
**Query**: Give me 5 creative titles for a sci-fi movie.
**Response**: "The Star Beyond the Void"
**Output**:
{
    "score": 0.2,
    "reason": "The user requested a specific quantity (5 titles), but the model only provided one. It failed to meet the volume constraint."
}
"""

    def build_judge_data(self, index: int, input: Any, output: Any) -> str:
        prompt = super().build_judge_data(index, input, output)

        required_aspects = input.get("required_aspects", [])
        aspects_text = ""
        if required_aspects:
            aspects_text = f"\nmust be covered:\n" + "\n".join([f"- {aspect}" for aspect in required_aspects])

        prompt += f"\n{aspects_text}"
        return prompt


@scorer_register(
    ValidationMetrics.OUTPUT_QUALITY,
    model_config=ModelConfig(
        llm_provider=os.getenv("VALIDATE_LLM_PROVIDER", os.getenv("LLM_PROVIDER")),
        llm_model_name=os.getenv("VALIDATE_LLM_MODEL_NAME", os.getenv("LLM_MODEL_NAME")),
        llm_temperature=float(os.getenv("VALIDATE_LLM_TEMPERATURE", os.getenv("LLM_TEMPERATURE", "0.7"))),
        llm_base_url=os.getenv("VALIDATE_LLM_BASE_URL", os.getenv("LLM_BASE_URL")),
        llm_api_key=os.getenv("VALIDATE_LLM_API_KEY", os.getenv("LLM_API_KEY")),
    )
)
class OutputQualityScorer(OutputLlmScore):
    """Comprehensive evaluation of answer quality

    Contain:
        Correctness
        Relevance
        Integrity
        Clarity
        Professionalism
    """

    def __init__(self, eval_config: EvaluationConfig = None, model_config: ModelConfig = None):
        super().__init__(name=ValidationMetrics.OUTPUT_QUALITY, eval_config=eval_config, model_config=model_config)

    def _build_judge_system_prompt(self) -> str:
        return """# Role
You are an expert AI Quality Auditor. Your task is to evaluate the quality of a [Model Response] based on a [User Query] and a [Reference Answer] (if provided).

# Scoring Framework
You must evaluate the response across 5 specific dimensions. For each dimension, assign a raw score from **0.0 to 1.0**. Then, calculate the **Weighted Final Score**.

## Dimensions & Weights
1. **Correctness (40%)**:
   - Is the information factually accurate? Does it align with the Reference Answer?
   - *Weight: 0.4*
2. **Relevance (20%)**:
   - Does it directly answer the user's specific question without going off-topic?
   - *Weight: 0.2*
3. **Completeness (20%)**:
   - Does it address all constraints and sub-questions? Is context missing?
   - *Weight: 0.2*
4. **Clarity (10%)**:
   - Is the logic clear? Is the formatting (lists, bolding) effective?
   - *Weight: 0.1*
5. **Professionalism (10%)**:
   - Is the tone objective and polite? Is the terminology precise?
   - *Weight: 0.1*

## Quality Rubric (Based on Final Score)
Determine the quality label based on the calculated Final Score:
- **0.90 - 1.00**: Excellent (优秀)
- **0.80 - 0.89**: Good (良好)
- **0.60 - 0.79**: Medium (中等)
- **0.40 - 0.59**: Pass (及格)
- **0.00 - 0.39**: Fail (不及格)

# Calculation Formula
Final Score = (Correctness × 0.4) + (Relevance × 0.2) + (Completeness × 0.2) + (Clarity × 0.1) + (Professionalism × 0.1)

# Output Format
Please strictly output in the following JSON format:
{
    "dimension_scores": {
        "correctness": <float 0.0-1.0>,
        "relevance": <float 0.0-1.0>,
        "completeness": <float 0.0-1.0>,
        "clarity": <float 0.0-1.0>,
        "professionalism": <float 0.0-1.0>
    },
    "score": <float 0.00-1.00>,
    "quality_label": "<string: Excellent/Good/Medium/Pass/Fail>",
    "reason": "<string>"
}

# Guidelines
- Be strict. A score of 1.0 implies perfection.
- In the "reason", explicitly state which dimension lowered the score if the result is not Excellent.
"""

    def build_judge_data(self, index: int, input: Any, output: Any) -> str:
        prompt = super().build_judge_data(index, input, output)
        ground_truth = input.get("ground_truth", "")

        prompt += f"\nReference Answer: {ground_truth}"
        return prompt
