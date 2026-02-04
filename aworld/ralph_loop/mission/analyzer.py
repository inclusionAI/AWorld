# coding: utf-8
# Copyright (c) inclusionAI.
import json
import traceback
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

import aworld
from aworld.config import ModelConfig
from aworld.logs.util import logger
from aworld.models.llm import get_llm_model, acall_llm_model
from aworld.models.model_response import ModelResponse
from aworld.ralph_loop.mission.types import Mission

mission_analyzer_system_prompt = """# Role
You are an expert **Task Analysis Engine**. Your objective is to parse user natural language input into a structured JSON configuration containing **Intent**, **Entities**, and **Complexity**.

# Analysis Rules

## 1. Intent Classification
Classify the task into one of the following standard categories. If the task is difficult to categorize or involves mixed intents, use `General_Task`.

- `Knowledge_QA`: Knowledge-based Q&A (Internal knowledge, no external tools).
- `Data_Retrieval`: Data retrieval (Web search, Database query).
- `Content_Creation`: Content generation (Writing, Translating, Polishing).
- `Code_Generation`: Code writing, debugging, or explanation.
- `Data_Analysis`: Data processing and analysis.
- `Logic_Reasoning`: Math or logical deduction.
- `ChitChat`: Casual conversation.
- `General`: General or ambiguous tasks (hard to distinguish).

## 2. Entity Extraction
Extract key information segments from the input. **Output only the `value` and `type`**.
The `type` must be selected **strictly** from this finite list:

- `Time`: Date, time, frequency, duration.
- `Location`: City, country, region, address.
- `Person`: Names, job titles.
- `Organization`: Companies, institutions, teams.
- `Topic`: Key topics, search queries, keywords.
- `Tool`: Software, programming languages, libraries, APIs.
- `Constraint`: Restrictions (format, word count, tone, forbidden items).
- `Quantity`: Numbers, amounts, currency.
- `File`: Filenames, file paths, file extensions.

## 3. Tool Suggestion
Predict the **generic types** of tools needed to accomplish this task. Use abstract capability names, not specific software brands.
**Examples of valid tool names**:
- `Web_Search` (For live info)
- `Code_Interpreter` (For math, data analysis, coding)
- `File_Reader` / `File_Writer` (For local file ops)
- `Image_Generator` (For visuals)
- `Calendar` (For scheduling)
- `Email_Client` (For sending/receiving)
- `Database_Connector` (For SQL/NoSQL)
- *Return an empty list `[]` if no tools are needed.*

## 4. Complexity Assessment
You must select exactly one level from the following **5 specific tiers**:

- **Trivial** (score 0.0-0.2): **One step**. Intuitive response, no thinking or tools required.
- **Low** (score 0.2-0.4): **Few steps**. Simple linear execution (e.g., Search -> Summarize), no complex planning.
- **Medium** (score 0.4-0.6): **Need plan**. Requires formulation of a simple plan or logical judgment, involves tool usage.
- **High** (score 0.6-0.8): **Plan and Task Decomposition**. Requires breaking the task into sub-tasks or complex reasoning.
- **Complex** (score 0.8-1.0): **Multi-stage**. Long-horizon task involving multi-agent collaboration, iterative loops, or high context dependency.

# Output Format
Return a strictly valid JSON object. Do not include markdown fencing (e.g., ```json).
{
  "intent": {
    "category": "String (Select from list)",
    "description": "String (Concise technical summary)"
  },
  "entities": {
    "type (Select from finite list)": [value (Extracted value)]
  },
  "suggested_tools": [
    "String (Generic Tool Name)"
  ],
  "complexity": {
    "level": "String (Trivial|Low|Medium|High|Complex)",
    "score": Float (0.0 - 1.0),
    "reasoning": "String (Brief explanation)"
  }
}

# Examples
## Example 1: Trivial
**Input**: "What's up?"
**Output**: 
{
  "intent": {
    "category": "ChitChat",
    "description": "Casual greeting."
  },
  "entities": [],
  "suggested_tools": [],
  "complexity": {
    "level": "Trivial",
    "score": 0.1,
    "reasoning": "Simple interaction requiring no planning."
  }
}

## Example 2: Medium (Search + File)
**Input**: "Find the top 3 French restaurants in NYC and book a table for 2 tonight."
**Output**: 
{
  "intent": {
    "category": "General_Task",
    "description": "Search for restaurant recommendations based on criteria and perform a reservation action."
  },
  "entities": {
    "Quantity": [2, 3],
    "Topic": ["French restaurants"],
    "Location": ["NYC"],
    "Time": ["tonight"]
  },
  "suggested_tools": [
    "Web_Search",
    "Calendar"
  ],
  "complexity": {
    "level": "Medium",
    "score": 0.55,
    "reasoning": "Requires a plan: Search -> Filter -> Book tool usage."
  }
}

## Example 3: Complex (Multi-stage)
**Input**: "Research the EV market trends in Europe for the last 5 years, write a comprehensive report, generate charts, and email it to the team."
**Output**: 
{
  "intent": {
    "category": "General",
    "description": "Complex workflow involving research, data analysis, content creation, and communication."
  },
  "entities": {
      "Topic": ["EV market trends"],
      "Constraint": ["charts", "comprehensive report"],
      "Time": ["last 5 years"],
      "Location": ["Europe"]
  },
  "suggested_tools": [
    "Web_Search",
    "Code_Interpreter",
    "Email_Client"
  ],
  "complexity": {
    "level": "Complex",
    "score": 0.9,
    "reasoning": "Multi-stage task requiring extensive planning, task decomposition (Research -> Write -> Visualize -> Send), and context maintenance."
  }
}

Please analyze the following user instruction:
**Input**:

"""


class Analyzer(ABC):
    @abstractmethod
    async def analyze(self, mission: Mission) -> Mission:
        """Analyze user input intent, task complexity, and related entity etc."""


class MissionAnalyzer(Analyzer):
    """LLM-based mission analyzer, it can be transformed into a tool or agent in the future."""

    def __init__(self, model_config: ModelConfig, system_prompt: str = mission_analyzer_system_prompt):
        self.model_config = model_config
        self.system_prompt = system_prompt
        self._llm = None

    async def analyze(self, mission: Mission) -> Mission:
        if not self._llm:
            self._llm = get_llm_model(conf=self.model_config)

        messages = self._build_analysis_input(mission)
        response = await acall_llm_model(self._llm, messages=messages)
        res_dict = self._parse_llm_response(response)
        return mission.update(**res_dict)

    def _build_analysis_input(self, mission: Mission) -> List[Dict[str, Any]]:
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": mission.text}
        ]

    def _parse_llm_response(self, response: ModelResponse) -> Dict[str, Any]:
        data = response.structured_output.get("parsed_json", {})
        if not data:
            content = response.content
            try:
                import re

                json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
                if json_match:
                    content = json_match.group(1)

                data = json.loads(content)
            except Exception as e:
                logger.warning(f"Failed to parse mission analysis response: {e}")
                if aworld.debug_mode:
                    logger.error(
                        f"Failed to parse mission analysis response {response.raw_response} \n{traceback.format_exc()}")
        return data
