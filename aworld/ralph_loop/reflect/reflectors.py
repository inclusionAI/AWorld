# coding: utf-8
# Copyright (c) inclusionAI.
import json
import traceback
from abc import ABC, abstractmethod
from typing import List, Dict, Any

import aworld
from aworld.config import ModelConfig
from aworld.logs.util import logger
from aworld.models.llm import get_llm_model, call_llm_model
from aworld.models.model_response import ModelResponse
from aworld.ralph_loop.reflect.types import (
    ReflectionInput,
    ReflectionType,
    ReflectionLevel,
    ReflectionResult,
)


class Reflector(ABC):
    """The base class of reflection can be extended to reflect on different types or dimensions.

    For example, failure reflection, performance evaluation, cost accounting, etc.
    """

    def __init__(
            self,
            name: str,
            reflection_type: ReflectionType,
            level: ReflectionLevel = ReflectionLevel.MEDIUM,
            priority: int = 10
    ):
        self.name = name
        self.reflection_type = reflection_type
        self.level = level
        self.priority = priority

    async def reflect(self, reflect_input: ReflectionInput) -> ReflectionResult:
        """Reflection template process.

        Reflection has three important steps: analysis, insight and suggestion.

        Args:
            reflect_input: Reflect input structure.

        Returns:
            ReflectionResult structure.
        """
        result = ReflectionResult(
            reflection_type=self.reflection_type,
            level=self.level
        )

        analysis = await self.analyze(reflect_input)
        insights = await self.insight(reflect_input, analysis)
        suggestions = await self.suggest(reflect_input, insights)

        result.summary = analysis.get("summary", "")
        result.key_findings = analysis.get("key_findings", [])
        result.root_causes = analysis.get("root_causes", [])
        result.insights = insights
        result.suggestions = suggestions
        result.metadata = {
            "analysis": analysis
        }

        return result

    @abstractmethod
    async def analyze(self, reflect_input: ReflectionInput) -> Dict[str, Any]:
        """Analyzing inputs that require reflection.

        Weak constraint, the result contains three keys: summary, key_findings, root_causes.

        Args:
            reflect_input: Input for reflect.

        Returns:
            Analysis results.
        """

    async def insight(self, reflect_input: ReflectionInput, analysis: Dict[str, Any]) -> List[str]:
        """Insight combines input and insight analysis results to discover valuable things.

        Args:
            reflect_input: Input for reflect.
            analysis: Analysis results.

        Returns:
            Insight list.
        """
        return analysis.get("insights", [])

    async def suggest(self, reflect_input: ReflectionInput, insights: List[str]) -> List[str]:
        """Generate suggestions based on insights and input, and guide what needs to be done next.

        Args:
            reflect_input: Input for reflect.
            insights: Insight list.

        Returns:
            Suggestion list.
        """
        return []


class GeneralReflector(Reflector):
    def __init__(
            self,
            model_config: ModelConfig,
            name: str = "general_reflector",
            reflection_type: ReflectionType = ReflectionType.OPTIMIZATION,
            level: ReflectionLevel = ReflectionLevel.DEEP,
            priority: int = 1
    ):
        super().__init__(
            reflection_type=reflection_type,
            name=name,
            level=level,
            priority=priority
        )

        self.model_config = model_config
        self._llm = None

    async def analyze(self, reflection_input: ReflectionInput) -> dict:
        if not self._llm:
            self._llm = get_llm_model(self.model_config)

        analysis_input = self._build_analysis_input(reflection_input)
        response = call_llm_model(self._llm, analysis_input)
        result = self._parse_response(response)
        return result

    def _build_system_prompt(self) -> str:
        return """You are a Deep Task Reflection Specialist.
Your responsibility is not to evaluate the quality, but to extract valuable experiences, lessons, and systematic improvement plans through dissecting the entire process of task execution.

# Goal
Please read the given 'Input', 'Iteration', 'Previous Attempts', 'Output', 'Error' (possibly) for a thorough audit.
You need to output a well structured review report, which must include the following 5 core sections:

# 1. Reflection Summary
- Definition: A high-level summary of the execution status of this task.
- Requirement: Define success or failure in one sentence (success/failure/partial success). Briefly describe whether the task has achieved its core objectives and whether there have been significant setbacks.
- Thinking guidance: Imagine you are giving a 30 second elevator presentation to the CEO.

# 2. Key Findings
- Definition: What happened that objectively exists during the execution process.
- Requirement: List 3-5 specific observation points. It can be an exception return of a certain tool, redundancy of a certain step, or an unexpected efficient path.
- Thought guidance: Do not write speculation, only facts. For example, in step 3, the search tool returned an empty result, causing subsequent steps to be retried three times

# 3. Root Cause Analysis
- Definition: The deep logic that leads to the above findings (Why it happened).
- Requirement: Distinguish between "surface causes" and "root causes".
- Thinking guidance: Use the "5 Why" analysis method.
- Surface level: The code is reporting an error.
- Deep level: The agent does not escape special characters entered by the user, and there is a lack of relevant security constraints in the System Prompt.

# 4. Strategic Insights
- Definition: So What is patterns or lessons extracted from this task that can be reused for the future.
- Requirement: To go beyond the current task itself and elevate to the level of strategy, cognition, or systems.
- Thought guidance:
    - Data level: What characteristics does this reveal about the data source?
    - Strategic level: Is the current Planning logic suitable for this type of task?
    - Cognitive level: Is there a systematic bias in our understanding of user intent?
    
# 5. Actionable Suggestions for Improvement
-Definition: A specific action guide for the future (Now What).
-Requirement: It must be implementable and executable. Divided into "immediate repair (for current tasks)" and "long-term optimization (for system evolution)".
-Thought guidance: Don't say 'optimize by improving accuracy', say 'add<Example>tag in Prompt to standardize output format'.

# Requirement
key_findings, root_cause,insights, suggestions do not necessarily have values, but if there are values, they must ensure correctness and authenticity.

# Output Format
You must strictly output a **single valid JSON object**. Do not include markdown fencing (like ```json) or preamble text.
root_cause, insights, suggestions

## JSON Schema
{
  "summary": "string: A concise executive summary of the execution.",
  "key_findings": [
    "string: Observation 1",
    "string: Observation 2"
  ],
  "root_cause": [
    "fundamental cause. The deep, underlying systemic reason."
  ],
  "insights": [
    "string: Insight 1",
    "string: Insight 2"
  ],
  "suggestions": [
    "immediate fix suggestion. Actionable step to resolve the current issue.",
  ]
}
"""

    def _build_analysis_input(self, reflection_input: ReflectionInput) -> List[Dict[str, Any]]:
        status = "Success" if reflection_input.success else "Failed"

        if reflection_input.success:
            input_str = """Analyze this execution and extract learnings:"""
        else:
            input_str = """Analyze this failed execution and identify root causes:"""

        input_str += f"""
Execution Status: {status}
Iteration: {reflection_input.iteration}
Previous Attempts: {reflection_input.previous_attempts}

Input:
{reflection_input.input_data}

Output:
{reflection_input.output_data}
"""

        if not reflection_input.success and reflection_input.error_msg:
            input_str += f"\nError:\n{reflection_input.error_msg}"

        results = [
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "user", "content": input_str}
        ]
        return results

    def _parse_response(self, response: ModelResponse) -> dict:
        data = response.structured_output.get("parsed_json")
        if not data:
            content = response.content
            try:
                import re

                json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
                if json_match:
                    content = json_match.group(1)

                data = json.loads(content)
            except Exception as e:
                logger.warning(f"Failed to parse reflection response: {e}")
                if aworld.debug_mode:
                    logger.error(
                        f"Failed to parse reflection response {response.raw_response} \n{traceback.format_exc()}")
                return {
                    "summary": response,
                    "key_findings": [],
                    "root_causes": [],
                    "insights": [],
                    "suggestions": []
                }
        return data
