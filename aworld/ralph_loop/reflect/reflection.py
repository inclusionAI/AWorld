# coding: utf-8
# Copyright (c) inclusionAI.
import traceback
from typing import List, Optional

import aworld
from aworld.logs.util import logger
from aworld.ralph_loop.reflect.reflectors import Reflector
from aworld.ralph_loop.reflect.types import (
    ReflectionInput,
    ReflectionResult,
    ReflectionHistory,
    ReflectionType,
)


class Reflection:
    """Coordinate multiple reflectors to perform task/process reflection.

    Reflection manages a set of Reflector instances, selects appropriate ones, executes and maintains a history of reflection results.
    """

    def __init__(self, reflectors: Optional[List[Reflector]] = None):
        # List of available reflectors, sorted by priority (lower value = higher priority)
        self.reflectors = reflectors or []
        self.reflectors.sort(key=lambda r: r.priority)
        # History object to store past reflection results
        self.history = ReflectionHistory()

    def add_reflector(self, reflector: Reflector):
        """Add a new reflector to the list and keep the list sorted by priority."""
        self.reflectors.append(reflector)
        self.reflectors.sort(key=lambda r: r.priority)

    async def reflect(self,
                      reflect_input: ReflectionInput,
                      reflection_types: Optional[List[ReflectionType]] = None) -> List[ReflectionResult]:
        """Run all valid reflectors on the given input and collect their results.

        Args:
            reflect_input: The input used for reflection, include relevant info: raw input, model output, trajectory, etc.
            reflection_types: Type of reflect, different types of reflection may lead to different conclusions.

        Returns:
            ReflectionResult item list.
        """
        results = []

        # Select reflectors based on input and type
        valid_reflectors = self._select_reflectors(reflect_input, reflection_types)
        logger.info(f"Running {len(valid_reflectors)} reflectors")

        for reflector in valid_reflectors:
            if aworld.debug_mode:
                logger.info(f"Running reflector: {reflector.name}")

            try:
                # Each reflector performs its reflection asynchronously
                result = await reflector.reflect(reflect_input)
                results.append(result)
                # Add result to history
                self.history.add_reflection(result)
            except Exception as e:
                logger.error(f"Reflector {reflector.name} failed: {e}")
                if aworld.debug_mode:
                    logger.error(f"Reflector {reflector.name} failed: {traceback.format_exc()}")

        return results

    def _select_reflectors(self,
                           reflect_input: ReflectionInput,
                           reflection_types: Optional[List[ReflectionType]]) -> List[Reflector]:
        """Select reflectors to run based on input and optional type filter.

        - If types are specified, only use those reflectors.
        - If input indicates failure, prefer FAILURE/OPTIMIZATION reflectors.
        - Otherwise, use all reflectors.
        """
        if reflection_types:
            return [r for r in self.reflectors if r.reflection_type in reflection_types]

        if not reflect_input.success:
            return [
                r for r in self.reflectors
                if r.reflection_type in [ReflectionType.FAILURE, ReflectionType.OPTIMIZATION]
            ]

        return self.reflectors

    def get_history(self, num: int = 10) -> List[ReflectionResult]:
        """Get the most recent N reflection results from history."""
        return self.history.get_recent(num)

    def get_by_type(self, reflection_type: ReflectionType) -> List[ReflectionResult]:
        """Get all reflection results of a specific type from history."""
        return self.history.get_by_type(reflection_type)

    def summarize(self) -> dict:
        """Summarize the reflection history: total, success, failure, and success rate."""
        return {
            "total_reflections": self.history.total_count,
            "success_count": self.history.success_count,
            "failure_count": self.history.failure_count,
            "success_rate": self.history.success_count / self.history.total_count if self.history.total_count > 0 else 0,
        }
