# coding: utf-8
# Copyright (c) inclusionAI.
from typing import Optional, List

from aworld.logs.util import logger
from aworld.ralph_loop.detect.stop_condition import StopCondition, build_stop_conditions
from aworld.ralph_loop.detect.types import StopState, StopDecision


class CompositeStopDetector:
    """A combined termination detector that coordinates multiple detectors and performs detection in priority order.

    This class combining multiple stop condition detectors into a unified detection interface.
    The stop condition detectors are sorted by priority, and once a detector triggers a stop condition, it immediately returns the decision result.
    """

    def __init__(self, conditions: Optional[List[StopCondition]] = None):
        conditions = build_stop_conditions(custom_conditions=conditions)
        # low value first
        self.detectors = sorted(conditions, key=lambda d: d.priority)

    async def should_stop(self, state: StopState) -> StopDecision:
        """Use logical short-circuit mechanism to check if the execution should be stopped.

        Traverse all stop condition detectors in priority order, and return immediately once a detector returns a stop decision.

        Args:
            state: Current stop status information.
            
        Returns:
            StopDecision: Stop decision object, including whether to stop and reason information
        """
        for detector in self.detectors:
            decision = await detector.safe_check(state)
            # Logic circuit breaker
            if decision.should_stop:
                logger.info(
                    f"Stop detector trigger: {detector.__class__.__name__} | "
                    f"reason: {decision.stop_type.value} | "
                    f"message: {decision.reason}"
                )
                return decision

        return StopDecision(should_stop=False)

    def add_condition(self, condition: StopCondition):
        """Add new stop conditions, automatically inserted into the detector list in priority order.
        
        Args:
            condition: Stop condition instance.
        """
        self.detectors.append(condition)
        self.detectors.sort(key=lambda d: d.priority)

    def remove_condition(self, condition_class: type):
        """Remove stop condition detectors of the specified type.
        
        Args:
            condition_class: Detector class type to be removed.
        """
        self.detectors = [d for d in self.detectors if not isinstance(d, condition_class)]

    def enable_condition(self, condition_class: type):
        """Enable stop condition detectors of the specified type.
        
        Args:
            condition_class: Detector class type to be enabled.
        """
        for detector in self.detectors:
            if isinstance(detector, condition_class):
                detector.enabled = True

    def disable_condition(self, condition_class: type):
        """Disable stop condition detectors of the specified type.

        Args:
            condition_class: Detector class type to be disabled.
        """
        for detector in self.detectors:
            if isinstance(detector, condition_class):
                detector.enabled = False


def create_stop_detector(enable_completion: bool = True,
                         enable_limits: bool = True,
                         enable_failure_detection: bool = True,
                         enable_interrupt: bool = True,
                         enable_error: bool = True,
                         custom_detectors: Optional[List[StopCondition]] = None) -> CompositeStopDetector:
    """Utility func to build and return a composite stop detector instance based on the specified configuration options.

    Support flexible configuration of the enabled status of various detection conditions.

    Returns:
        Composite Stop Detector: An instance of a composite stop detector
    """
    conditions = build_stop_conditions(enable_completion, enable_limits,
                                       enable_failure_detection, enable_interrupt,
                                       enable_error, custom_detectors)
    return CompositeStopDetector(conditions)
