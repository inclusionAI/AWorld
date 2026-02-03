# coding: utf-8
# Copyright (c) inclusionAI.

"""
The validation module is mainly used to evaluate scores from different dimensions.
The framework supports indicators of various dimension types such as Format, Logic, Output, Quality,
Trajectory, Compliance, Plan, Code, etc.

Key Validators:
- FormatValidationScorer: Validates format correctness (JSON, XML, YAML, etc.) and schema compliance
- LogicConsistencyScorer: Evaluates logical consistency, reasoning validity, and constraint satisfaction
- OutputCorrectnessScorer: Validates output correctness against ground truth or expected keywords
- OutputRelevanceScorer: Evaluates output relevance to the input query
- OutputCompletenessScorer: Checks if output is complete and addresses all aspects
- TrajectoryStructureScorer: Validates trajectory data structure and required fields
- TrajectoryQualityScorer: Overall trajectory quality assessment using LLM

"""

# TODO: If there is sufficient universal stability, it will be moved to the evaluation module
