# coding: utf-8
# Copyright (c) inclusionAI.
import json
import os
from typing import List, Callable, Optional

import torch
from transformers import pipeline, AutoModelForSequenceClassification, AutoTokenizer

from aworld.config import ModelConfig
from aworld.logs.util import logger
from aworld.models.llm import get_llm_model, call_llm_model
from train.integration.common import semantic_similarity


class CompositeReward:
    """Composite reward for combining multiple reward functions with weighted sum,
     the final reward is calculated as the weighted sum of all individual reward functions.

    Reward function format:
        >>> reward_fn(completions: List[str], prompts: List[str], **kwargs) -> List[float]
    """

    def __init__(
            self,
            reward_fns: List[Callable[[List[str], List[str]], List[float]]],
            weights: Optional[List[float]] = None,
    ) -> None:
        if not reward_fns:
            raise ValueError("reward_fns must not be empty")
        self.reward_fns = reward_fns
        self.weights = weights or [1.0] * len(reward_fns)
        if len(self.weights) != len(self.reward_fns):
            raise ValueError("weights length must match reward_fns length")

    def __call__(self, completions: List[str], prompts: List[str], **kwargs) -> List[float]:
        """Calculate composite reward scores.

        Args:
            completions: List of model-generated text completions
            prompts: Corresponding list of prompt texts

        Returns:
            List of composite reward scores for each completion.

        Raises:
            ValueError: If any reward function returns a different number of scores than completions
        """
        if not completions:
            return []
        # Remove structures such as list and dict
        for idx, completion in enumerate(completions):
            if isinstance(completion, list):
                completion = completion[0]
                if isinstance(completion, dict):
                    completion = completion.get('content')
                    completions[idx] = completion
            elif isinstance(completion, dict):
                completion = completion.get('content')
                completions[idx] = completion

        all_scores: List[List[float]] = []
        for reward_fn in self.reward_fns:
            scores = reward_fn(completions, prompts, **kwargs)
            if len(scores) != len(completions):
                raise ValueError("All reward functions must return scores for each completion")
            all_scores.append(scores)
        # weighted sum
        totals: List[float] = [0.0] * len(completions)
        for w, scores in zip(self.weights, all_scores):
            for i, s in enumerate(scores):
                totals[i] += w * float(s)
        logger.info(f"Composite reward scores: {totals}, \ncompletions: {completions}")
        return totals


def build_reward_model_fn(
        reward_model_name: str,
        local: bool = True,
        normalize: bool = True,
) -> Callable[[List[str], List[str]], List[float]]:
    """Build a reward function based on a pre-trained reward model.

    Args:
        reward_model_name: Name or path of the pre-trained reward model.
        local: Use local model.
        normalize: Whether to z-normalize the reward scores (per batch).

    Returns:
        A reward function that takes (completions, prompts) and returns a list of reward scores
    """
    if local:
        return build_local_reward_model_fn(reward_model_name, normalize)
    else:
        return build_api_reward_model_fn(reward_model_name, normalize)


def build_api_reward_model_fn(
        reward_model_name: str,
        normalize: bool = True,
) -> Callable[[List[str], List[str]], List[float]]:
    llm_model = get_llm_model(ModelConfig(
        llm_model_name=reward_model_name,
        llm_api_key=os.environ.get("LLM_API_KEY"),
        llm_base_url=os.environ.get("LLM_BASE_URL"),
        llm_temperature=os.environ.get("LLM_TEMPERATURE", 0.2),
        llm_provider=os.environ.get("LLM_PROVIDER")
    ))

    def reward_fn(completions: List[str], prompts: List[str], **kwargs) -> List[float]:
        solutions = kwargs.get("solution", prompts)

        outputs = []
        for idx, completion in enumerate(completions):
            prompt = eval_prompt.replace("{{key_points}}", solutions[idx])
            prompt = prompt.replace("{{model_response}}", completion)
            messages = [{"role": "user", "content": prompt},]
            response = call_llm_model(llm_model, messages=messages)
            content = response.content.replace("```json", "").replace("```", "")
            outputs.append(json.loads(content))

        scores: List[float] = []
        for out in outputs:
            scores.append(float(out.get("score", 0.)))
        if not normalize or len(scores) < 2 or all(x == 0. for x in scores):
            return scores

        # z-norm for stability (per-batch)
        t = torch.tensor(scores, dtype=torch.float32)
        std = float(t.std().clamp(min=1e-6))
        mean = float(t.mean())
        normed = ((t - mean) / std).tolist()
        return [float(x) for x in normed]

    return reward_fn


def build_local_reward_model_fn(
        reward_model_name: str,
        normalize: bool = True,
) -> Callable[[List[str], List[str]], List[float]]:

    rm_pipe = None

    def rm_call(solutions):
        if solutions:
            # use rules to evaluate model_resp and solution pair
            def generate(completions: List[str], **kwargs):
                results = []
                for idx, completion in enumerate(completions):
                    res = semantic_similarity(completion, solutions[idx])
                    results.append([{"score": res}])
                return results

            pipe = generate
        else:
            rm_tokenizer = AutoTokenizer.from_pretrained(reward_model_name, use_fast=True)
            # ensure padding token exists for batched inference
            if rm_tokenizer.pad_token is None:
                candidate = rm_tokenizer.eos_token or rm_tokenizer.sep_token or rm_tokenizer.cls_token or rm_tokenizer.unk_token
                if candidate is not None:
                    rm_tokenizer.pad_token = candidate
                else:
                    rm_tokenizer.add_special_tokens({"pad_token": "[PAD]"})

            rm_model = AutoModelForSequenceClassification.from_pretrained(reward_model_name,
                                                                          dtype=torch.float16,
                                                                          device_map="auto")
            if getattr(rm_model.config, "pad_token_id", None) is None and rm_tokenizer.pad_token_id is not None:
                rm_model.config.pad_token_id = rm_tokenizer.pad_token_id

            # use a pipeline for batching and device placement
            pipe = pipeline(
                task="text-classification",
                model=rm_model,
                tokenizer=rm_tokenizer,
                truncation=True,
                top_k=None,
                function_to_apply="none",  # use raw logits so we can map scores directly
                return_all_scores=True,
            )

        return pipe

    def reward_fn(completions: List[str], prompts: List[str], **kwargs) -> List[float]:
        # unused here
        del prompts

        # solutions = kwargs.get("solution")
        solutions = None
        rm_judge = rm_pipe
        if rm_judge is None:
            rm_judge = rm_call(solutions)

        outputs = rm_judge(completions, batch_size=kwargs.get("batch_size", 2))
        scores: List[float] = []
        for out in outputs:
            # If binary classifier, use logit of positive class; otherwise sum weighted by label index
            if len(out) == 1:
                scores.append(float(out[0]["score"]))
            else:
                # prefer last class as "more positive"
                scores.append(float(out[-1]["score"]))
        if not normalize or all(x == 0. for x in scores):
            return scores

        # z-norm for stability (per-batch)
        t = torch.tensor(scores, dtype=torch.float32)
        std = float(t.std().clamp(min=1e-6))
        mean = float(t.mean())
        normed = ((t - mean) / std).tolist()
        return [float(x) for x in normed]

    return reward_fn


eval_prompt = """# Role
You are an Adaptive AI Quality Assurance Judge. Your task is to evaluate the quality of a "Model Response" based on the available inputs.

# Input Data
1. **User Query**: The original instruction or question provided to the model.
2. **Key Points (Optional)**: A list of ground truth facts or requirements.
3. **Model Response**: The text generated by the model, which may contain `<think>` tags.

# Evaluation Workflow

## Phase 1: Critical Pre-check (Circuit Breaker)
**Applies to ALL cases.**
1. **Remove CoT**: Strip out all content between (and including) `<think>` and `</think>` tags.
2. **Check Existence**: 
   - **FATAL ERROR**: If the remaining text is empty or contains only whitespace/punctuation.
   - **Action**: Score **0.0** immediately.
   - **Reason**: "No Final Answer Provided."

## Phase 2: Mode Selection
Determine the evaluation mode based on the `Key Points` input:

### Mode A: Reference-Based Evaluation (If `Key Points` are provided)
Use strict fact-checking against the provided points.
- **Hit**: The response covers the point semantically.
- **Miss**: The point is missing.
- **Wrong**: The response contradicts the point.
- **Scoring Formula**: $$ Score = \frac{\text{Hit Count}}{\text{Total Key Points}} $$

### Mode B: Query-Based Evaluation (If `Key Points` are empty/null)
Use the `User Query` to assess the response quality based on **Helpfulness, Relevance, and Instruction Following**.
- **Criteria**:
  1. **Relevance**: Does it directly answer the user's specific question?
  2. **Constraints**: Did it follow formatting rules (e.g., "Output JSON", "Write in French")?
  3. **Completeness**: Is the answer comprehensive without hallucinations?
- **Scoring Rubric**:
  - **1.0 (Perfect)**: Fully addresses the query, follows all constraints, logic is sound.
  - **0.7 - 0.9 (Good)**: Correct answer but minor flaws (e.g., wordy, slight formatting miss).
  - **0.4 - 0.6 (Acceptable)**: Partially correct, misses a constraint, or includes minor hallucinations.
  - **0.1 - 0.3 (Poor)**: Irrelevant, hallucinated, or fails to address the core intent.
  - **0.0 (Failure)**: Complete refusal or nonsense.

# Output Format
Output a JSON object:
```json
{
    "has_final_answer": true | false,
    "evaluation": "Analysis of the final answer (after removing <think> content).",
    "missing_points": ["List missing key points OR specific constraints not met"],
    "score": 0.0 // Float between 0.0 and 1.0
}

# Examples
## Example 1 (Unrelated Answer)
**Key Points**: "Python is an interpreted language and supports object-oriented programming"
**Model Response**: "<think>Python compilation principle... Is it compiled? No.</think>Python is a fast-running language that is widely used in the field of AI."
**Output**:
```json
{
    "has_final_answer": true
    "evaluation": "The reply only mentioned the application field and speed, without including any given technical points, but it did mention its use in the AI field.",
    "missing_points": ["Python is an interpreted language", "Python supports object-oriented programming"],
    "score": 0.2
}
```

## Example 2 (The Partial Answer)
**Key Points**: "The capital of France is Paris, and the currency is the euro"
**Model Response**: "<think>France... capital Paris... is the currency franc? No, it's euro.</think>The capital of France is Paris."
**Output**:
```json
{
    "has_final_answer": true,
    "evaluation": "The model accurately answered the capital, but omitted the currency information. Note: The currency information is only within the "think" tag and is not counted towards the score.",
    "missing_points": ["The currency is the euro."],
    "score": 0.5
}
```

Example 3 (The "Think-Only" Failure)
**Key Points**: "An apple is a red fruit.""
**Model Response**: "<think>The color of apples... Most are red, but there are also green ones. Biologically, they belong to the Rosaceae family... I'm thinking about how to respond to the user..."
**Output**:
```json
{
    "has_final_answer": false,
    "evaluation": "The model response is empty after removing the thought chain, and no final answer for the user is provided. The circuit breaker mechanism has been triggered.",
    "missing_points": ["The apple is red", "The apple is a fruit"],
    "score": 0.0
}
```

Example 4 (The Perfect Answer)
**Key Points**: "Air pressure affects boiling point, and water boils at 100 degrees"
**Model Response**: "<think>Under standard atmospheric pressure...</think>Under standard atmospheric pressure, water typically boils at 100 degrees Celsius. However, it should be noted that the boiling point varies with changes in atmospheric pressure."
**Output**:
```json
{
    "has_final_answer": true,
    "evaluation": "The model's response accurately covers two key points: boiling temperature and the influence of atmospheric pressure.",
    "missing_points": [],
    "score": 1.0
}
```
# Current Task
**User Query** or **Key Points**:
{{key_points}}
**Model Response**:
{{model_response}}

# Output:
"""
