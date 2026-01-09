# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from train.integration.common import semantic_similarity


def verl_default_reward_func(data_source, solution_str, ground_truth, extra_info=None):
    """Default reward function."""
    return semantic_similarity(solution_str, ground_truth)
