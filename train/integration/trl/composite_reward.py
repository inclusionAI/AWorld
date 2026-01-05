# coding: utf-8
# Copyright (c) inclusionAI.
from typing import List, Callable, Optional

import torch
from transformers import pipeline, AutoModelForSequenceClassification, AutoTokenizer

from aworld.logs.util import logger


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
        device: Optional[str] = None,
        normalize: bool = True,
) -> Callable[[List[str], List[str]], List[float]]:
    """Build a reward function based on a pre-trained reward model.

    Args:
        reward_model_name: Name or path of the pre-trained reward model
        device: Device to run the model on, defaults to auto-selection
        normalize: Whether to z-normalize the reward scores (per batch)

    Returns:
        A reward function that takes (completions, prompts) and returns a list of reward scores
    """
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
    pipe_device = 0 if (device == "cuda" or (device is None and torch.cuda.is_available())) else -1
    rm_pipe = pipeline(
        task="text-classification",
        model=rm_model,
        tokenizer=rm_tokenizer,
        #  device=pipe_device,
        truncation=True,
        top_k=None,
        function_to_apply="none",  # use raw logits so we can map scores directly
        return_all_scores=True,
    )

    def reward_fn(completions: List[str], prompts: List[str], **kwargs) -> List[float]:
        # unused here
        del prompts
        outputs = rm_pipe(completions, batch_size=kwargs.get("batch_size", 2))
        scores: List[float] = []
        for out in outputs:
            # If binary classifier, use logit of positive class; otherwise sum weighted by label index
            if len(out) == 1:
                scores.append(float(out[0]["score"]))
            else:
                # prefer last class as "more positive"
                scores.append(float(out[-1]["score"]))
        if not normalize:
            return scores
        # z-norm for stability (per-batch)
        t = torch.tensor(scores, dtype=torch.float32)
        std = float(t.std().clamp(min=1e-6))
        mean = float(t.mean())
        normed = ((t - mean) / std).tolist()
        return [float(x) for x in normed]

    return reward_fn
