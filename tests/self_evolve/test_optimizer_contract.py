from __future__ import annotations

import pytest

from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.optimizers.base import OptimizerRequest
from aworld.self_evolve.optimizers.dspy_adapter import DSPyGEPAOptimizer, DSPyMIPROOptimizer
from aworld.self_evolve.optimizers.llm_mutator import TraceReflectiveLLMMutator
from aworld.self_evolve.trace_pack import build_trace_pack
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    EvaluationSummary,
    SelfEvolveTargetRef,
)


def _target() -> SelfEvolveTargetRef:
    return SelfEvolveTargetRef(target_type="skill", target_id="demo-skill", path="SKILL.md")


def _trace_pack():
    return build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                "state": {"input": {"content": "Fix browser login guidance."}},
                "action": {"content": "I will inspect login traces."},
                "reward": {"status": "ok"},
            },
            {
                "meta": {"step": 2, "agent_id": "agent", "pre_agent": "agent"},
                "state": {"messages": []},
                "action": {"content": "Login guidance did not mention CDP profile mismatch."},
                "reward": {"status": "failed"},
            },
        ],
        source_kind="current_trajectory",
        task_id="optimizer-task",
    )


def test_optimizer_request_exposes_trainable_cases_without_held_out_leakage() -> None:
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="train-1", input="train"),
            EvalCase(case_id="valid-1", input="valid"),
            EvalCase(case_id="held-1", input="held"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test"},
            split_seed="seed",
            splits={"train": ["train-1"], "validation": ["valid-1"], "held_out": ["held-1"]},
            trainable_case_ids=("train-1", "valid-1"),
            held_out_case_ids=("held-1",),
        ),
    )

    request = OptimizerRequest.from_dataset(
        target=_target(),
        current_content="# Demo\n",
        target_fingerprint="sha256:old",
        trace_packs=(_trace_pack(),),
        validation_feedback=(
            EvaluationSummary(
                variant_id="baseline",
                metrics={"score": 0.4},
                dataset_split="validation",
            ),
        ),
        dataset=dataset,
    )

    assert [case.case_id for case in request.trainable_cases] == ["train-1", "valid-1"]
    assert "held-1" not in repr(request)
    assert request.prior_feedback == ()


@pytest.mark.asyncio
async def test_trace_reflective_llm_mutator_proposes_candidate_and_lineage() -> None:
    prompts = []

    async def mutate(prompt: str) -> dict:
        prompts.append(prompt)
        return {
            "content": "# Demo\n\nMention CDP profile mismatch before retrying login.\n",
            "rationale": "The trace shows repeated browser login mismatch.",
        }

    request = OptimizerRequest(
        target=_target(),
        current_content="# Demo\n\nOld guidance.\n",
        target_fingerprint="sha256:old",
        trace_packs=(_trace_pack(),),
        validation_feedback=(
            EvaluationSummary(
                variant_id="baseline",
                metrics={"score": 0.4},
                dataset_split="validation",
            ),
        ),
        prior_feedback=(
            EvaluationSummary(
                variant_id="candidate-previous",
                metrics={
                    "score": 35.0,
                    "failed_gates": ["evidence_quality"],
                },
                dataset_split="historical",
            ),
        ),
        trainable_cases=(EvalCase(case_id="train-1", input="login task"),),
        max_candidates=1,
    )

    optimizer = TraceReflectiveLLMMutator(mutate_text=mutate)
    result = await optimizer.propose(request)

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert isinstance(candidate, CandidateVariant)
    assert candidate.candidate_id.startswith("llm-mutator-")
    assert candidate.content.endswith("retrying login.\n")
    assert candidate.target_fingerprint == "sha256:old"
    assert result.lineage[0].candidate_id == candidate.candidate_id
    assert result.lineage[0].optimizer_name == "trace-reflective-llm-mutator"
    assert result.lineage[0].trainable_case_ids == ("train-1",)
    assert "optimizer-task:step-2" in prompts[0]
    assert "prior_feedback" in prompts[0]
    assert "candidate-previous" in prompts[0]
    assert "evidence_quality" in prompts[0]
    assert "evidence-preservation" in prompts[0]
    assert "tool-agnostic" in prompts[0]
    assert "persist raw evidence to files or artifacts first" in prompts[0]
    assert "bounded structured summaries" in prompts[0]
    assert "compacted/truncated outputs as unusable evidence" in prompts[0]
    assert "evidence ledger" in prompts[0]
    assert "claim-by-claim" in prompts[0]
    assert "held-1" not in prompts[0]


@pytest.mark.asyncio
async def test_llm_mutator_compacts_feedback_before_prompting() -> None:
    prompts = []

    async def mutate(prompt: str) -> dict:
        prompts.append(prompt)
        return {
            "content": "# Demo\n\nUse artifact-first evidence extraction.\n",
            "rationale": "Compacted evidence feedback requires stronger preservation.",
        }

    long_tool_output = "raw-tool-output-" + ("x" * 8000)
    request = OptimizerRequest(
        target=_target(),
        current_content="# Demo\n\nOld guidance.\n",
        target_fingerprint="sha256:old",
        trace_packs=(_trace_pack(),),
        validation_feedback=(
            EvaluationSummary(
                variant_id="candidate-compacted",
                metrics={
                    "score": 34.0,
                    "failed_gates": ["evidence_quality", "score_improvement"],
                    "evidence_compacted": True,
                    "evidence_incomplete": True,
                    "evidence_block_count": 3,
                    "evidence_issues": [
                        "tool output compacted for context reuse",
                        long_tool_output,
                    ],
                    "raw_tool_output": long_tool_output,
                    "messages": [{"role": "tool", "content": long_tool_output}],
                },
                dataset_split="validation",
            ),
        ),
        trainable_cases=(EvalCase(case_id="train-1", input="web task"),),
    )

    optimizer = TraceReflectiveLLMMutator(mutate_text=mutate)
    await optimizer.propose(request)

    assert "feedback_summary" in prompts[0]
    assert "required_behaviors" in prompts[0]
    assert "artifact_first" in prompts[0]
    assert "bounded_structured_summary" in prompts[0]
    assert "claim_evidence_ledger" in prompts[0]
    assert "raw_tool_output" not in prompts[0]
    assert long_tool_output not in prompts[0]
    assert "x" * 1000 not in prompts[0]


@pytest.mark.asyncio
async def test_llm_mutator_filters_noop_candidates() -> None:
    async def mutate(prompt: str) -> dict:
        return {"content": "# Demo\n\nOld guidance.\n", "rationale": "No change."}

    optimizer = TraceReflectiveLLMMutator(mutate_text=mutate)
    result = await optimizer.propose(
        OptimizerRequest(
            target=_target(),
            current_content="# Demo\n\nOld guidance.\n",
            target_fingerprint="sha256:old",
            trace_packs=(_trace_pack(),),
        )
    )

    assert result.candidates == ()
    assert result.diagnostics["filtered_noop_candidates"] == 1


@pytest.mark.asyncio
async def test_dspy_adapter_missing_dependency_fails_only_when_selected() -> None:
    optimizer = DSPyGEPAOptimizer(import_module=lambda name: (_ for _ in ()).throw(ImportError(name)))

    with pytest.raises(ImportError, match="DSPy optimizer 'gepa' requires optional dependency 'dspy'"):
        await optimizer.propose(
            OptimizerRequest(
                target=_target(),
                current_content="# Demo\n",
                target_fingerprint="sha256:old",
                trace_packs=(_trace_pack(),),
            )
        )


@pytest.mark.asyncio
async def test_dspy_gepa_adapter_delegates_when_dependency_is_available() -> None:
    class FakeDSPy:
        @staticmethod
        def GEPA(request):
            return {
                "content": "# Demo\n\nGEPA candidate.\n",
                "rationale": "GEPA improved instructions.",
            }

    optimizer = DSPyGEPAOptimizer(import_module=lambda name: FakeDSPy)
    result = await optimizer.propose(
        OptimizerRequest(
            target=_target(),
            current_content="# Demo\n",
            target_fingerprint="sha256:old",
            trace_packs=(_trace_pack(),),
        )
    )

    assert result.candidates[0].content.endswith("GEPA candidate.\n")
    assert result.lineage[0].optimizer_name == "dspy-gepa"


@pytest.mark.asyncio
async def test_dspy_mipro_adapter_delegates_when_dependency_is_available() -> None:
    class FakeDSPy:
        @staticmethod
        def MIPRO(request):
            return {
                "content": "# Demo\n\nMIPRO candidate.\n",
                "rationale": "MIPRO improved few-shot examples.",
            }

    optimizer = DSPyMIPROOptimizer(import_module=lambda name: FakeDSPy)
    result = await optimizer.propose(
        OptimizerRequest(
            target=_target(),
            current_content="# Demo\n",
            target_fingerprint="sha256:old",
            trace_packs=(_trace_pack(),),
        )
    )

    assert result.candidates[0].content.endswith("MIPRO candidate.\n")
    assert result.lineage[0].optimizer_name == "dspy-mipro"
