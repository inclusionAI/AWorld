# coding: utf-8
# Copyright (c) inclusionAI.

class MetricNames:
    """Supported metrics name in AWorld.

    Supports indicators of various dimension types such as Format, Logic, Output, Quality,
    Trajectory, Compliance, Plan, Code
    """
    LABEL_DISTRIBUTION = 'label_distribution'
    SUMMARIZE_QUALITY = 'summarize_quality'
    PREDICT_TIME_COST_MS = 'predict_time_cost_ms'

    # Format
    FORMAT_CORRECTNESS = "format_correctness"
    SCHEMA_COMPLIANCE = "schema_compliance"

    # Logic
    LOGIC_CONSISTENCY = "logic_consistency"
    REASONING_VALIDITY = "reasoning_validity"
    CONSTRAINT_SATISFACTION = "constraint_satisfaction"

    # Output (answer)
    OUTPUT_CORRECTNESS = "output_correctness"
    OUTPUT_RELEVANCE = "output_relevance"
    OUTPUT_COMPLETENESS = "output_completeness"
    OUTPUT_QUALITY = "output_quality"
    OUTPUT_LENGTH = "output_length"

    # Trajectory
    TRAJECTORY_STRUCTURE = "trajectory_structure"
    TRAJECTORY_TOOL_CALLS = "trajectory_tool_calls"
    TRAJECTORY_COMPLETENESS = "trajectory_completeness"
    TRAJECTORY_EFFICIENCY = "trajectory_efficiency"
    TRAJECTORY_QUALITY = "trajectory_quality"

    # Compliance
    POLICY_COMPLIANCE = "policy_compliance"
    SECURITY_COMPLIANCE = "security_compliance"
    STANDARD_COMPLIANCE = "standard_compliance"
    REGULATION_COMPLIANCE = "regulation_compliance"

    # Quality
    READABILITY = "readability"
    PROFILE = "profile"

    # Code (Special)
    CODE_QUALITY = "code_quality"
    CODE_SECURITY = "code_security"
    CODE_STYLE = "code_style"

    # Plan (Special)
    PLAN_FEASIBILITY = "plan_feasibility"
    PLAN_COMPLETENESS = "plan_completeness"
    PLAN_CLARITY = "plan_clarity"
