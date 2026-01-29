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
    """Coordinate multiple reflectors."""

    def __init__(self, reflectors: Optional[List[Reflector]] = None):
        self.reflectors = reflectors or []
        self.reflectors.sort(key=lambda r: r.priority)
        self.history = ReflectionHistory()

    def add_reflector(self, reflector: Reflector):
        self.reflectors.append(reflector)
        self.reflectors.sort(key=lambda r: r.priority)

    async def reflect(self,
                      reflect_input: ReflectionInput,
                      reflection_types: Optional[List[ReflectionType]] = None) -> List[ReflectionResult]:
        results = []

        valid_reflectors = self._select_reflectors(reflect_input, reflection_types)
        logger.info(f"Running {len(valid_reflectors)} reflectors")

        for reflector in valid_reflectors:
            if aworld.debug_mode:
                logger.info(f"Running reflector: {reflector.name}")

            try:
                result = await reflector.reflect(reflect_input)
                results.append(result)
                self.history.add_reflection(result)
            except Exception as e:
                logger.error(f"Reflector {reflector.name} failed: {e}")
                if aworld.debug_mode:
                    logger.error(f"Reflector {reflector.name} failed: {traceback.format_exc()}")

        return results

    def _select_reflectors(self,
                           reflect_input: ReflectionInput,
                           reflection_types: Optional[List[ReflectionType]]) -> List[Reflector]:
        if reflection_types:
            return [r for r in self.reflectors if r.reflection_type in reflection_types]

        if not reflect_input.success:
            return [
                r for r in self.reflectors
                if r.reflection_type in [ReflectionType.FAILURE, ReflectionType.OPTIMIZATION]
            ]

        return self.reflectors

    def get_history(self, num: int = 10) -> List[ReflectionResult]:
        return self.history.get_recent(num)

    def get_by_type(self, reflection_type: ReflectionType) -> List[ReflectionResult]:
        return self.history.get_by_type(reflection_type)

    def summarize(self) -> dict:
        return {
            "total_reflections": self.history.total_count,
            "success_count": self.history.success_count,
            "failure_count": self.history.failure_count,
            "success_rate": self.history.success_count / self.history.total_count if self.history.total_count > 0 else 0,
        }
