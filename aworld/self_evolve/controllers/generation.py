"""Candidate-generation request planning.

This is the first generation boundary extracted from the run coordinator.  It
owns concrete model-request estimation; scheduling and optimizer execution stay
in the runner until their state contract is made explicit.
"""

from __future__ import annotations

from dataclasses import dataclass

from aworld.core.context.amni.prompt.assembly.budget import (
    BudgetedPromptAssemblyProvider,
)
from aworld.self_evolve.candidate_generation import (
    CANDIDATE_GENERATION_SYSTEM_PROMPT,
)
from aworld.self_evolve.optimizers.base import CandidateOptimizer, OptimizerRequest
from aworld.self_evolve.optimizers.llm_mutator import (
    TraceReflectiveLLMMutator,
    build_mutation_prompt,
)


@dataclass(frozen=True)
class CandidateGenerationController:
    """Plans the concrete model-request budget for a candidate population."""

    output_tokens_per_candidate: int
    model_name: str = "gpt-4o"

    def __post_init__(self) -> None:
        if (
            isinstance(self.output_tokens_per_candidate, bool)
            or self.output_tokens_per_candidate <= 0
        ):
            raise ValueError("output_tokens_per_candidate must be positive")
        if not isinstance(self.model_name, str) or not self.model_name.strip():
            raise ValueError("model_name must be non-empty")
        object.__setattr__(self, "model_name", self.model_name.strip())

    def request_derived_tokens(
        self,
        optimizer: CandidateOptimizer,
        request: OptimizerRequest,
    ) -> int | None:
        """Estimate built-in population requests, or defer to fallback policy."""

        if not isinstance(optimizer, TraceReflectiveLLMMutator):
            return None
        if optimizer.population_callable is None:
            # Custom serial mutators may be deterministic and do not expose an
            # LLM request contract. Keep the configured/observed fallback.
            return None
        total = 0
        for candidate_index in range(request.max_candidates):
            prompt = build_mutation_prompt(
                request,
                candidate_index=candidate_index,
            )
            estimate = BudgetedPromptAssemblyProvider.estimate_request_tokens(
                messages=[
                    {
                        "role": "system",
                        "content": CANDIDATE_GENERATION_SYSTEM_PROMPT,
                    },
                    {"role": "user", "content": prompt},
                ],
                tools=[],
                model_name=self.model_name,
            )
            total += int(estimate["total"]) + self.output_tokens_per_candidate
        return total


def candidate_generation_request_derived_tokens(
    optimizer: CandidateOptimizer,
    request: OptimizerRequest,
    *,
    output_tokens_per_candidate: int,
    model_name: str = "gpt-4o",
) -> int | None:
    """Compatibility helper for callers migrating to the controller."""

    return CandidateGenerationController(
        output_tokens_per_candidate=output_tokens_per_candidate,
        model_name=model_name,
    ).request_derived_tokens(optimizer, request)
