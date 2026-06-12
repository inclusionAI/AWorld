"""Candidate optimizer contracts for self-evolve."""

from aworld.self_evolve.optimizers.base import (
    CandidateOptimizer,
    OptimizerRequest,
    OptimizerResult,
)
from aworld.self_evolve.optimizers.dspy_adapter import DSPyGEPAOptimizer, DSPyMIPROOptimizer
from aworld.self_evolve.optimizers.llm_mutator import TraceReflectiveLLMMutator

__all__ = [
    "CandidateOptimizer",
    "DSPyGEPAOptimizer",
    "DSPyMIPROOptimizer",
    "OptimizerRequest",
    "OptimizerResult",
    "TraceReflectiveLLMMutator",
]
