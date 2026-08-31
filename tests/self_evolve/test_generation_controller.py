from dataclasses import replace

import pytest

from aworld.self_evolve.controllers.generation import (
    CandidateGenerationController,
)
from aworld.self_evolve.evolution_context import compile_evolution_context
from aworld.self_evolve.optimizers.base import OptimizerRequest
from aworld.self_evolve.optimizers.llm_mutator import TraceReflectiveLLMMutator
from aworld.self_evolve.types import SelfEvolveTargetRef


def _optimizer_request(*, max_candidates: int) -> OptimizerRequest:
    request = OptimizerRequest(
        target=SelfEvolveTargetRef("skill", "demo"),
        current_content="# Demo\n\n" + ("bounded behavior evidence\n" * 200),
        target_fingerprint="sha256:demo",
        trace_packs=(),
        max_candidates=max_candidates,
    )
    return replace(
        request,
        evolution_context=compile_evolution_context(request),
    )


def test_generation_controller_plans_population_prompt_and_completion_tokens() -> None:
    async def population(*_args, **_kwargs):
        raise AssertionError("planning must not execute generation")

    controller = CandidateGenerationController(
        output_tokens_per_candidate=4_000,
        model_name="gpt-4o",
    )
    optimizer = TraceReflectiveLLMMutator(
        mutate_text=lambda _prompt: {},
        population_callable=population,
    )

    tokens = controller.request_derived_tokens(
        optimizer,
        _optimizer_request(max_candidates=2),
    )

    assert tokens is not None
    assert tokens > 8_000


def test_generation_controller_defers_custom_serial_mutator_to_fallback() -> None:
    controller = CandidateGenerationController(
        output_tokens_per_candidate=4_000,
    )
    optimizer = TraceReflectiveLLMMutator(
        mutate_text=lambda _prompt: {},
        population_callable=None,
    )

    assert (
        controller.request_derived_tokens(
            optimizer,
            _optimizer_request(max_candidates=1),
        )
        is None
    )


@pytest.mark.parametrize(
    ("output_tokens", "model_name", "message"),
    (
        (0, "gpt-4o", "output_tokens_per_candidate"),
        (4_000, " ", "model_name"),
    ),
)
def test_generation_controller_rejects_invalid_request_envelope(
    output_tokens: int,
    model_name: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        CandidateGenerationController(
            output_tokens_per_candidate=output_tokens,
            model_name=model_name,
        )
