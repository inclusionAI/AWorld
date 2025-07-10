# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc


class BasePlanner:
    """Base planner classes."""
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def plan(self, context: "Context") -> str:
        """Plan subsequent execution steps based on context."""

    @abc.abstractmethod
    def replan(self, context: "Context") -> str:
        """Replan subsequent execution steps based on context."""
