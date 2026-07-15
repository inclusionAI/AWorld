from __future__ import annotations

import json

from aworld.self_evolve.datasets import EvalCase
from aworld.self_evolve.evolution_context import (
    EVOLUTION_CONTEXT_SCHEMA_VERSION,
    compile_evolution_context,
)
from aworld.self_evolve.lessons import LessonRecord
from aworld.self_evolve.optimizers.base import OptimizerRequest
from aworld.self_evolve.replay_adaptation import ReplayCapabilityRequirement
from aworld.self_evolve.types import EvaluationSummary, SelfEvolveTargetRef


def _request() -> OptimizerRequest:
    duplicate_feedback = EvaluationSummary(
        variant_id="candidate-old",
        dataset_split="validation",
        metrics={
            "failed_gates": ["replay_adaptation"],
            "required_behaviors": ["publish_missing_capability"],
            "candidate_score": 60.0,
            "baseline_score": 80.0,
        },
    )
    return OptimizerRequest(
        target=SelfEvolveTargetRef(
            target_type="skill",
            target_id="demo",
            path="/workspace/skills/demo/SKILL.md",
        ),
        current_content="# Demo\n\nExisting guidance.\n",
        target_fingerprint="sha256:current",
        trace_packs=(),
        validation_feedback=(duplicate_feedback,),
        prior_feedback=(duplicate_feedback,),
        lesson_records=(
            LessonRecord(
                lesson_id="lesson-1",
                lesson_type="required_runtime_behavior",
                title="Publish missing capability",
                summary="Publish the generic capability required by replay.",
                metrics={"required_behaviors": ["publish_missing_capability"]},
            ),
        ),
        trainable_cases=(
            EvalCase(
                case_id="train-1",
                input="perform the recorded task",
                expected_output="task completed",
            ),
        ),
        replay_requirements=(
            ReplayCapabilityRequirement(
                requirement_id="requirement-1",
                kind="stateful_tool",
                identifier="tool:recorded-state",
                case_ids=("train-1",),
                evidence_refs=("event:1",),
                status="unbound",
                detail="requires deterministic replay",
            ),
        ),
        target_package_inventory=("SKILL.md",),
    )


def test_compiler_deduplicates_feedback_and_selects_typed_strategies() -> None:
    context = compile_evolution_context(_request())

    assert context.schema_version == EVOLUTION_CONTEXT_SCHEMA_VERSION
    assert len(context.validation_feedback) == 1
    assert context.population_strategies == (
        "minimal_behavior_delta",
        "missing_capability_completion",
        "quality_regression_repair",
        "efficiency_and_robustness",
    )
    assert [item["capability_type"] for item in context.capability_contracts] == [
        "replay"
    ]


def test_prompt_payload_is_bounded_canonical_and_contains_no_held_out_cases() -> None:
    context = compile_evolution_context(_request())

    payload = context.to_prompt_payload(candidate_index=1)

    assert payload["schema_version"] == EVOLUTION_CONTEXT_SCHEMA_VERSION
    assert payload["candidate_index"] == 1
    assert payload["population_strategy"] == "missing_capability_completion"
    assert payload["expected_output"]["schema_version"] == (
        "aworld.self_evolve.candidate.v1"
    )
    assert [item["case_id"] for item in payload["trainable_cases"]] == ["train-1"]
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert "held_out" not in serialized
    assert len(serialized) < 100_000


def test_optimizer_request_can_carry_compiled_context_without_changing_dataset_fields() -> None:
    request = _request()
    context = compile_evolution_context(request)

    carried = OptimizerRequest(
        **{
            **request.__dict__,
            "evolution_context": context,
        }
    )

    assert carried.evolution_context is context
    assert carried.trainable_cases == request.trainable_cases
    assert carried.replay_requirements == request.replay_requirements
