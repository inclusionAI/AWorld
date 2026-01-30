# coding: utf-8
# Copyright (c) inclusionAI.
import asyncio
import traceback

from abc import ABC, abstractmethod
from typing import Optional, List

import aworld
from aworld.logs.util import logger
from aworld.ralph_loop.detect.types import StopState, StopDecision, StopType


class StopCondition(ABC):
    """Termination condition base class.

    Ensure atomic detection as much as possible and avoid logical coupling.
    """

    def __init__(self, priority: int = 5):
        self.priority = priority
        self.enabled = True

    @abstractmethod
    async def should_stop(self, state: StopState) -> StopDecision:
        """Check if it should be terminated.

        Args:
            state: Terminate detection state

        Returns:
            StopDecision: Terminate Decision

        Errors:
            May throw exceptions, call `safe_check` can be used more safely.
        """

    async def safe_check(self, state: StopState) -> StopDecision:
        if not self.enabled:
            return StopDecision(should_stop=False)

        try:
            return await self.should_stop(state)
        except Exception as e:
            logger.error(f"Stop checker {self.__class__.__name__} failed: {e}.")
            if aworld.debug_mode:
                logger.error(f"trace: {traceback.format_exc()}")
            return StopDecision(
                should_stop=False,
                metadata={"error": str(e), "detector": self.__class__.__name__}
            )


class CompletionCondition(StopCondition):
    """Complete Condition Detector - Check if the task has been successfully completed."""

    def __init__(self):
        super().__init__(priority=3)

    async def should_stop(self, state: StopState) -> StopDecision:
        confirmations = state.loop_state.completion_confirmations
        confirmation_threshold = state.loop_state.confirmation_threshold

        if confirmations >= confirmation_threshold:
            return StopDecision(
                should_stop=True,
                stop_type=StopType.COMPLETION,
                reason=f"Mission completed (confirm: {confirmations})",
                confidence=min(1.0, confirmations / confirmation_threshold),
                metadata={"confirmations": confirmations}
            )

        return StopDecision(should_stop=False)


class CustomStopCondition(StopCondition):
    def __init__(self):
        super().__init__(priority=3)

    async def should_stop(self, state: StopState) -> StopDecision:
        custom_stop_fn = state.completion_criteria.custom_stop

        if custom_stop_fn and callable(custom_stop_fn):
            if asyncio.iscoroutinefunction(custom_stop_fn):
                should_stop = await custom_stop_fn(state)
            else:
                should_stop = custom_stop_fn(state)

            if should_stop:
                return StopDecision(
                    should_stop=True,
                    stop_type=StopType.CUSTOM_STOPPED,
                    reason="Meet custom termination criteria",
                    metadata={"custom_function": custom_stop_fn.__name__}
                )

        return StopDecision(should_stop=False)


class MaxIterationsCondition(StopCondition):
    def __init__(self):
        super().__init__(priority=4)

    async def should_stop(self, state: StopState) -> StopDecision:
        max_iters = state.completion_criteria.max_iterations
        current_iter = state.loop_state.iteration

        if 0 < max_iters <= current_iter:
            return StopDecision(
                should_stop=True,
                stop_type=StopType.MAX_ITERATIONS,
                reason=f"Reaching the maximum number of iterations: ({current_iter}/{max_iters})",
                metadata={"current": current_iter, "max": max_iters}
            )

        return StopDecision(should_stop=False)


class TimeoutCondition(StopCondition):
    def __init__(self):
        super().__init__(priority=4)

    async def should_stop(self, state: StopState) -> StopDecision:
        timeout = state.completion_criteria.timeout
        elapsed = state.elapsed_time()

        if 0 < timeout <= elapsed:
            return StopDecision(
                should_stop=True,
                stop_type=StopType.TIMEOUT,
                reason=f"Execution timeout: ({elapsed:.1f}s/{timeout}s)",
                metadata={"elapsed": elapsed, "timeout": timeout}
            )

        return StopDecision(should_stop=False)


class MaxCostCondition(StopCondition):
    def __init__(self):
        super().__init__(priority=4)

    async def should_stop(self, state: StopState) -> StopDecision:
        max_cost = state.completion_criteria.max_cost
        current_cost = state.loop_state.cumulative_cost

        if 0 < max_cost <= current_cost:
            return StopDecision(
                should_stop=True,
                stop_type=StopType.MAX_COST,
                reason=f"Achieve maximum cost: ({current_cost:.3f}/{max_cost:.3f})",
                metadata={"current": current_cost, "max": max_cost}
            )

        return StopDecision(should_stop=False)


class MaxEndlessCondition(StopCondition):
    def __init__(self):
        super().__init__(priority=4)

    async def should_stop(self, state: StopState) -> StopDecision:
        """Check if there is a progression free loop."""
        max_endless = state.completion_criteria.max_endless
        # todo

        return StopDecision(should_stop=False)


class ConsecutiveFailuresCondition(StopCondition):
    def __init__(self):
        super().__init__(priority=2)

    async def should_stop(self, state: StopState) -> StopDecision:
        max_failures = state.completion_criteria.max_consecutive_failures
        current_failures = state.loop_state.consecutive_failures

        if 0 < max_failures <= current_failures:
            return StopDecision(
                should_stop=True,
                stop_type=StopType.MAX_CONSECUTIVE_FAILURES,
                reason=f"Too many consecutive failures: ({current_failures}/{max_failures})",
                metadata={"current": current_failures, "max": max_failures}
            )

        return StopDecision(should_stop=False)


class ValidationFailureCondition(StopCondition):
    def __init__(self):
        super().__init__(priority=2)

    async def should_stop(self, state: StopState) -> StopDecision:
        # TODO
        return StopDecision(should_stop=False)


class InterruptCondition(StopCondition):
    def __init__(self):
        super().__init__(priority=1)


class UserInterruptCondition(InterruptCondition):
    async def should_stop(self, state: StopState) -> StopDecision:
        # check interrupt file
        interrupt_marker = state.loop_context.loop_dir() / ".interrupt"

        if interrupt_marker.exists():
            try:
                interrupt_marker.unlink()
            except:
                pass

            return StopDecision(
                should_stop=True,
                stop_type=StopType.USER_INTERRUPTED,
                reason="Detected user interrupt request"
            )

        return StopDecision(should_stop=False)


class ExternalSignalCondition(InterruptCondition):

    def __init__(self):
        import signal

        super().__init__()
        self.signal_received = False

        # Ctrl+C
        signal.signal(signal.SIGINT, self.on_signal)
        # kill
        signal.signal(signal.SIGTERM, self.on_signal)

    def on_signal(self, signum, frame):
        self.signal_received = True
        # detail process...

    async def should_stop(self, state: StopState) -> StopDecision:
        if self.signal_received:
            return StopDecision(
                should_stop=True,
                stop_type=StopType.EXTERNAL_SIGNAL,
                reason="Received external termination signal"
            )

        return StopDecision(should_stop=False)


class ErrorCondition(StopCondition):
    def __init__(self):
        super().__init__(priority=0)


class SystemErrorCondition(ErrorCondition):
    async def should_stop(self, state: StopState) -> StopDecision:
        if state.metadata.get("system_error"):
            error_msg = state.metadata.get("error_message", "Unknown system error")
            return StopDecision(
                should_stop=True,
                stop_type=StopType.SYSTEM_ERROR,
                reason=f"System error: {error_msg}",
                metadata={"error": error_msg}
            )

        return StopDecision(should_stop=False)


class ResourceExhaustedCondition(ErrorCondition):
    def __init__(self, memory_threshold_mb: int = 1024, disk_threshold_mb: int = 1000):
        super().__init__()
        self.memory_threshold = memory_threshold_mb * 1024 * 1024
        self.disk_threshold = disk_threshold_mb * 1024 * 1024

    async def should_stop(self, state: StopState) -> StopDecision:
        try:
            import psutil

            # memory
            memory = psutil.virtual_memory()
            if memory.available < self.memory_threshold:
                return StopDecision(
                    should_stop=True,
                    stop_type=StopType.RESOURCE_EXHAUSTED,
                    reason=f"Insufficient available memory: ({memory.available / 1024 / 1024:.0f}MB)",
                    metadata={"resource": "memory", "available_mb": memory.available / 1024 / 1024}
                )

            # disk
            disk = psutil.disk_usage(state.loop_context.workspace)
            if disk.free < self.disk_threshold:
                return StopDecision(
                    should_stop=True,
                    stop_type=StopType.RESOURCE_EXHAUSTED,
                    reason=f"Insufficient available disk space: ({disk.free / 1024 / 1024:.0f}MB)",
                    metadata={"resource": "disk", "free_mb": disk.free / 1024 / 1024}
                )
        except ImportError:
            logger.warning("no psutil lib.")

        return StopDecision(should_stop=False)


class CompositeStopDetector:
    """Combination termination detector, coordinate multiple detectors and perform detection in priority order."""

    def __init__(self, detectors: Optional[List[StopCondition]] = None):
        if not detectors:
            detectors = build_stop_detectors()

        self.detectors = sorted(detectors, key=lambda d: d.priority)

    async def should_stop(self, state: StopState) -> StopDecision:
        for detector in self.detectors:
            decision = await detector.safe_check(state)
            # Logic circuit breaker
            if decision.should_stop:
                logger.info(
                    f"Terminate detector trigger: {detector.__class__.__name__} | "
                    f"reason: {decision.stop_type.value} | "
                    f"message: {decision.reason}"
                )
                return decision

        return StopDecision(should_stop=False)

    def add_detector(self, detector: StopCondition):
        self.detectors.append(detector)
        self.detectors.sort(key=lambda d: d.priority)

    def remove_detector(self, detector_class: type):
        self.detectors = [d for d in self.detectors if not isinstance(d, detector_class)]

    def enable_detector(self, detector_class: type):
        for detector in self.detectors:
            if isinstance(detector, detector_class):
                detector.enabled = True

    def disable_detector(self, detector_class: type):
        for detector in self.detectors:
            if isinstance(detector, detector_class):
                detector.enabled = False


def build_stop_detectors(enable_completion: bool = True,
                         enable_limits: bool = True,
                         enable_failure_detection: bool = True,
                         enable_interrupt: bool = True,
                         enable_error: bool = True,
                         custom_detectors: Optional[List[StopCondition]] = None) -> List[StopCondition]:
    """Utility function for creating termination detectors.

    Args:
        enable_completion: Whether to enable completion detection
        enable_limits: whether to enable restriction detection
        enable_failure_detection: Whether to enable failure detection
        enable_interrupt: Whether to enable interrupt detection
        enable_error: Whether to enable system error detection
        custom_detectors: List of custom detectors

    Returns:
        Stop detector list.
    """
    detectors = []

    # built-in detectors
    if enable_interrupt:
        detectors.extend([
            UserInterruptCondition(),
            ExternalSignalCondition(),
        ])

    if enable_error:
        detectors.extend([
            SystemErrorCondition(),
            ResourceExhaustedCondition(),
        ])

    if enable_completion:
        detectors.extend([
            CompletionCondition(),
            CustomStopCondition(),
        ])

    if enable_limits:
        detectors.extend([
            MaxIterationsCondition(),
            TimeoutCondition(),
            MaxCostCondition(),
            MaxEndlessCondition(),
        ])

    if enable_failure_detection:
        detectors.extend([
            ConsecutiveFailuresCondition(),
            ValidationFailureCondition(),
        ])

    # custom detectors
    if custom_detectors:
        detectors.extend(custom_detectors)

    return detectors


def create_stop_detector(enable_completion: bool = True,
                         enable_limits: bool = True,
                         enable_failure_detection: bool = True,
                         enable_interrupt: bool = True,
                         enable_error: bool = True,
                         custom_detectors: Optional[List[StopCondition]] = None) -> CompositeStopDetector:
    # Return the detection of the combination
    detectors = build_stop_detectors(enable_completion, enable_limits,
                                     enable_failure_detection, enable_interrupt,
                                     enable_error, custom_detectors)
    return CompositeStopDetector(detectors)
