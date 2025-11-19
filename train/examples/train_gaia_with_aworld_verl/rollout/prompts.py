import os

from aworld.logs.util import logger

GAIA_SYSTEM_PROMPT = os.getenv("GAIA_SYSTEM_PROMPT")
logger.info("GAIA_SYSTEM_PROMPT", GAIA_SYSTEM_PROMPT)

episode_memory_summary_rule="""
1. Identify major milestones, subgoal completions, and strategic decisions
2. Extract only the most critical events that provide experience for long-term goals
"""

episode_memory_summary_schema="""
```json
{{
  "task_description": "A general summary of what the reasoning history has been doing and the overall goals it has been striving for.",
  "key_events": [
    {{
      "step": "step number",
      "description": "A detailed description of the specific action taken, decision made, or milestone achieved at this step, including relevant context and reasoning behind the choice.",
      "outcome": "A detailed account of the direct result, observation, or feedback received from this action or decision, including any new information gained or changes in the task state."
    }},
    ...
  ],
  "current_progress": "A general summary of the current progress of the task, including what has been completed and what is left to be done."
}}
```
"""

working_memory_summary_rule="""
1. Extract ONLY immediate goals, current challenges, and next steps
2. Ignore completed/historical information
"""

working_memory_summary_schema="""
```json
{{
  "immediate_goal": "A clear summary of the current subgoal—what you are actively working toward at this moment.",
  "current_challenges": "A concise summary of the main obstacles or difficulties you are presently encountering.",
  "next_actions": [
    {{
      "type": "tool_call/planning/decision",
      "description": "Anticipate and describe the next concrete action you intend to take to advance the task."
    }},
    ...
  ]
}}
```
"""

tool_memory_summary_rule="""
1. Analyze successful/unsuccessful tool patterns
2. Extract metadata about each tool's:
   - Effective parameter combinations
   - Common failure modes
   - Typical response structures
"""

tool_memory_summary_schema="""
```json
{{
  "tools_used": [
    {{
      "tool_name": "string",
      "success_rate": "float",
      "effective_parameters": ["param1", "param2"],
      "common_errors": ["error_type1", "error_type2"],
      "response_pattern": "description of typical output",
      "experience": "Reflect and summarize your experience using this tool, including both successes and failures."
    }},
    ...
  ],
  "derived_rules": [
    "When X condition occurs, prefer tool Y",
    "Tool Z works best with parameter A set to B",
    ...
  ]
}}
```
"""
