# coding: utf-8
# Copyright (c) inclusionAI.

"""
The detect module is used to detect stop conditions from different dimensions.
The framework supports stop condition detection such as Completion, Limits (max iterations, timeout, cost),
Failures (consecutive failures, validation failures), Interrupts (user, external signal),
and System Errors (resource exhausted, system errors).

Key StopCondition:
- CompletionCondition: Detects task completion based on confirmation threshold
- MaxIterationsCondition: Detects when max iterations limit is reached
- TimeoutCondition: Detects execution timeout
- MaxCostCondition: Detects when max cost limit is reached
- ConsecutiveFailuresCondition: Detects too many consecutive failures
- UserInterruptCondition: Detects user interrupt requests
- SystemErrorCondition: Detects system errors
- CompositeStopDetector: Coordinates multiple detectors and performs detection in priority order

Priority Levels (lower value = higher priority):
- 0: System errors (highest priority)
- 1: User interrupts
- 2: Failure conditions
- 3: Completion conditions
- 4: Limit conditions (lowest priority)

Usage:
    from aworld.runners.ralph_loop.detect import (
        create_stop_detector,
        build_stop_detectors,
        StopCondition,
        CompletionCondition,
        MaxIterationsCondition,
        CompositeStopDetector
    )

    # Create default detector with all built-in conditions
    detector = create_stop_detector()

    # Create custom detector with specific conditions
    custom_detectors = [MyCustomCondition()]
    detector = create_stop_detector(
        enable_completion=True,
        enable_limits=True,
        custom_detectors=custom_detectors
    )

    # Check stop condition
    decision = await detector.should_stop(stop_state)
    if decision.should_stop:
        print(f"Stop reason: {decision.reason}")
"""
