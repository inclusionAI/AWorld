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

Usage:
    from aworld.runners.ralph_loop.validate import (
        ValidationMetrics,
        FormatValidationScorer,
        LogicConsistencyScorer,
        OutputCorrectnessScorer,
        TrajectoryStructureScorer,
        DelegateEvalTarget
    )

    # Create validators
    format_validator = FormatValidationScorer()
    logic_validator = LogicConsistencyScorer(model_config=model_config)

    # Validate output
    result = await format_validator.score(index=0, input=input_data, output=output_data)
    if result.metric_results["format_correctness"]["value"] >= 0.8:
        print("Format validation passed")

    # Use DelegateEvalTarget to avoid re-prediction
    target = DelegateEvalTarget(output={...})
    eval_result = await target.predict(index=0, input=input_case)
"""
