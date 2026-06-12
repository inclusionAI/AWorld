"""Candidate optimizer contracts for self-evolve."""

from aworld.self_evolve.optimizers.base import (
    CandidateOptimizer,
    OptimizerRequest,
    OptimizerResult,
)
from aworld.self_evolve.optimizers.dspy_adapter import DSPyGEPAOptimizer
from aworld.self_evolve.optimizers.llm_mutator import TraceReflectiveLLMMutator

__all__ = [
    "CandidateOptimizer",
    "DSPyGEPAOptimizer",
    "OptimizerRequest",
    "OptimizerResult",
    "TraceReflectiveLLMMutator",
]
