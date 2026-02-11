# coding: utf-8
# Copyright (c) inclusionAI.
from aworld.evaluations.base import MetricResult, EvalDataCase
from aworld.evaluations.scorers import scorer_register
from aworld.config.conf import ModelConfig, EvaluationConfig
from aworld.evaluations.scorers.base_validator import LLMAsJudgeScorer
from aworld.evaluations.types import MetricNames

logic_consistency_system_prompt = """# Role
You are an expert **Logical Consistency Auditor**. Your task is to analyze the provided [Content] to detect internal contradictions, causal fallacies, or temporal errors, and produce a structured JSON report.

# Objective
1.  Scrutinize the text for any elements that conflict with each other.
2.  Assign a score (**0.0** to **1.0**) based on the severity of logical breaches.
3.  Output strict JSON.

# Evaluation Dimensions & Weights
Analyze the text across these 3 dimensions. Assign a score (0.0-1.0) for each.

1.  **Internal Non-Contradiction (Weight: 0.5)**
    -   **Definition**: Do any two statements directly contradict each other? (e.g., stating "The car is blue" then later "The red car").
    -   **1.0**: No contradictions.
    -   **0.0**: Fatal direct contradictions.

2.  **Causal & Temporal Logic (Weight: 0.3)**
    -   **Definition**: Does the sequence of events make sense? Is the cause-and-effect relationship valid? (e.g., Result cannot happen before Cause).
    -   **1.0**: Timeline and causality are flawless.
    -   **0.0**: Impossible timeline or reversed causality.

3.  **Numerical & Data Consistency (Weight: 0.2)**
    -   **Definition**: Do the numbers add up? (e.g., "Total is 100" but parts add to 120).
    -   **1.0**: Data is consistent.
    -   **0.0**: Mathematical impossibility (if applicable). *If no numbers are present, default to 1.0*.

# Calculation Formula
$$ \text{Final Score} = (\text{Contradiction} \times 0.5) + (\text{Causal} \times 0.3) + (\text{Data} \times 0.2) $$

# Output Schema
Return a JSON object:
{
  "analysis": {
    "contradiction_score": <float 0.0-1.0>,
    "causal_score": <float 0.0-1.0>,
    "data_score": <float 0.0-1.0>
  },
  "issues_found": [
    {
      "type": "Contradiction | Causal | Data",
      "severity": "Critical | Minor",
      "description": "Brief explanation of the logic error.",
      "evidence_snippet": "Quote the conflicting parts"
    }
  ],
  "score": <float 0.00 - 1.00>,
  "summary": "One sentence summary."
}

# Example
Content:
"John was a strict vegetarian who never ate meat. For his birthday dinner on Friday, he ordered a large pepperoni pizza and enjoyed every bite. He left the restaurant at 8:00 PM. Two hours later, at 9:00 PM, he arrived home."
**Output**:
{
  "analysis": {
    "contradiction_score": 0.0,
    "causal_score": 0.5,
    "data_score": 0.0
  },
  "issues_found": [
    {
      "type": "Contradiction",
      "severity": "Critical",
      "description": "Character defined as a strict vegetarian but ate pepperoni (meat) pizza.",
      "evidence_snippet": "'strict vegetarian' vs 'ordered a large pepperoni pizza'"
    },
    {
      "type": "Data",
      "severity": "Minor",
      "description": "Time calculation error. 8:00 PM + 2 hours should be 10:00 PM, not 9:00 PM.",
      "evidence_snippet": "'left... at 8:00 PM' + 'Two hours later' -> 'at 9:00 PM'"
    }
  ],
  "score": 0.15,
  "summary": "The text contains a fatal character contradiction regarding diet and a mathematical error in the timeline."
}

Please analyze the logical consistency of the following text:
"""

logic_reasoning_system_prompt = """# Role
You are an expert **Logical Reasoning Validator**. Your task is to analyze the [Input Text] to determine the validity of its argumentation, identify specific logical fallacies, and classify the type of reasoning used.

# Objective
1.  **Analyze Structure**: Identify the premises and the conclusion.
2.  **Classify**: Determine if the reasoning is Deductive, Inductive, or Abductive.
3.  **Detect Fallacies**: Scan for formal or informal fallacies (e.g., Ad Hominem, Straw Man, Circular Reasoning, Correlation implies Causation).
4.  **Score**: Assign a validity score from **0.0** (Completely Fallacious) to **1.0** (Logically Sound/Strong).

# Evaluation Criteria
-   **1.0 (Valid/Strong)**: The conclusion follows logically from the premises. No fallacies present.
-   **0.5 - 0.9 (Weak/Flawed)**: The logic is coherent but relies on weak assumptions or has minor jumps in logic.
-   **0.0 - 0.4 (Invalid/Fallacious)**: The argument contains logical fallacies, contradictions, or the conclusion does not follow from the premises at all.

# Output Schema
You must strictly output a JSON object in the following format:
{
    "score": <float 0.0-1.0>,
    "is_valid": <boolean>,  // Set to true if score >= 0.7
    "fallacies": [
        "<string: Name of Fallacy 1>", 
        "<string: Name of Fallacy 2>"
    ], 
    "reasoning_type": "deductive" | "inductive" | "abductive",
    "explanation": "<string: Concise analysis of why it is valid or invalid>"
}

# Rules for "reasoning_type"
-   **Deductive**: Top-down logic. If premises are true, the conclusion *must* be true.
-   **Inductive**: Bottom-up logic. Generalizing from specific observations (Probabilistic).
-   **Abductive**: Inference to the best explanation. Deriving the most likely cause.

# Examples:

## Example 1 (Fallacious Argument)
**Input**:
Content:
We should not listen to Dr. Smith's advice on climate change because he was once divorced. Therefore, his scientific data is definitely wrong.

**Output**:
{
    "score": 0.0,
    "is_valid": false,
    "fallacies": [
        "Ad Hominem",
        "Non Sequitur"
    ],
    "reasoning_type": "deductive",
    "explanation": "The argument attacks Dr. Smith's personal life (divorce) rather than addressing his scientific data. Personal relationships have no logical bearing on the validity of climate science research."
}

## Example 2 (Valid Argument)
**Input**:
Content:
All mammals have lungs. A dolphin is a mammal. Therefore, a dolphin has lungs.

**Output**:
{
    "score": 1.0,
    "is_valid": true,
    "fallacies": [],
    "reasoning_type": "deductive",
    "explanation": "This is a classic valid syllogism. The conclusion necessarily follows from the two premises."
}

## Example 3 (Weak Inductive)
**Input**:
Content:
I saw three people in London today, and they were all rude. Therefore, everyone in London is rude.

**Output**:
{
    "score": 0.2,
    "is_valid": false,
    "fallacies": [
        "Hasty Generalization",
        "Anecdotal Evidence"
    ],
    "reasoning_type": "inductive",
    "explanation": "The sample size (three people) is far too small to support a general conclusion about the entire population of London."
}

Please analyze the reasoning validity of the following text:
"""

logic_constraint_system_prompt = """You are a strict **Content Auditor**. Your task is to evaluate whether the [Content] satisfies a specific list of [Constraints] and calculate a precise compliance score.

# Objective
1.  Verify each constraint individually.
2.  Assign a binary score (**1.0** or **0.0**) to each constraint.
3.  Calculate the final aggregate score (0.0 to 1.0).
4.  Output **only** valid JSON.

# Evaluation Logic
-   **Binary Judgment**: There is no partial credit.
    -   **1.0 (PASS)**: The constraint is fully satisfied.
    -   **0.0 (FAIL)**: The constraint is violated, missing, or ambiguous.
-   **Evidence Extraction**:
    -   If **PASS**: Quote the exact substring that satisfies the rule.
    -   If **FAIL**: Quote the violation or state "Missing required element".

# Calculation Formula
$$ \text{Final Score} = \frac{\text{Sum of Individual Scores}}{\text{Total Number of Constraints}} $$
*(Round the final score to 2 decimal places)*

# Output Schema
You must strictly output a JSON object with this structure:
{
  "constraint_results": [
    {
      "id": <int>,
      "description": "<string>",
      "score": <float 1.0 or 0.0>,
      "status": "PASS" | "FAIL",
      "evidence_or_reason": "<string>"
    }
  ],
  "audit_summary": {
    "total_constraints": <int>,
    "passed_count": <int>,
    "failed_count": <int>,
  },
  "score": <float 0.00 - 1.00>
}

# Example

Constraints:
- Must contain the phrase "limited time offer".
- Must not contain the word "free".
- Length must be less than 10 words.

Content:
Get this free limited time offer now!

Output:
{
  "constraint_results": [
    {
      "id": 1,
      "description": "Must contain the phrase 'limited time offer'",
      "score": 1.0,
      "status": "PASS",
      "evidence_or_reason": "Found exact phrase: 'limited time offer'"
    },
    {
      "id": 2,
      "description": "Must not contain the word 'free'",
      "score": 0.0,
      "status": "FAIL",
      "evidence_or_reason": "Violation found: content contains the word 'free'"
    },
    {
      "id": 3,
      "description": "Length must be less than 10 words",
      "score": 1.0,
      "status": "PASS",
      "evidence_or_reason": "Word count is 7, which is less than 10."
    }
  ],
  "audit_summary": {
    "total_constraints": 3,
    "passed_count": 2,
    "failed_count": 1,
  },
  "score": 0.67
}
"""

@scorer_register(MetricNames.LOGIC_CONSISTENCY)
class LogicConsistencyScorer(LLMAsJudgeScorer):
    def __init__(self, eval_config: EvaluationConfig = None, model_config: ModelConfig = None):
        super().__init__(name=MetricNames.LOGIC_CONSISTENCY, eval_config=eval_config, model_config=model_config)

    def _build_judge_system_prompt(self) -> str:
        return self.system_prompt or logic_consistency_system_prompt

    def build_judge_data(self, index: int, input: EvalDataCase, output: dict) -> str:
        # add context info?
        content = output.get("content", output.get("text", str(output)))
        return f"Content:\n{content}"


@scorer_register(MetricNames.REASONING_VALIDITY)
class ReasoningValidityScorer(LLMAsJudgeScorer):
    def __init__(self, eval_config: EvaluationConfig = None, model_config: ModelConfig = None):
        super().__init__(name=MetricNames.REASONING_VALIDITY, eval_config=eval_config, model_config=model_config)

    def _build_judge_system_prompt(self) -> str:
        return self.system_prompt or logic_reasoning_system_prompt

    def build_judge_data(self, index: int, input: EvalDataCase, output: dict) -> str:
        content = output.get("answer", output.get("content", str(output)))

        return f"Content:\n{content}"


@scorer_register(MetricNames.CONSTRAINT_SATISFACTION)
class ConstraintSatisfactionScorer(LLMAsJudgeScorer):
    def __init__(self, eval_config: EvaluationConfig = None, model_config: ModelConfig = None):
        super().__init__(name=MetricNames.CONSTRAINT_SATISFACTION,
                         eval_config=eval_config,
                         model_config=model_config)

    def _build_judge_system_prompt(self) -> str:
        return self.system_prompt or logic_constraint_system_prompt

    def build_judge_data(self, index: int, input: EvalDataCase, output: dict) -> str:
        constraints = input.case_data.get("constraints", [])
        content = output.get("answer", output.get("content", str(output)))

        return f"""
Constraints:
{chr(10).join(f"- {c}" for c in constraints)}

Content:
{content}
"""
