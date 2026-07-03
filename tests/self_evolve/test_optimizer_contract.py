from __future__ import annotations

import pytest

from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.feedback import normalize_feedback_summary
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
async def test_llm_mutator_turns_low_efficiency_feedback_into_generic_strategy() -> None:
    prompts = []

    async def mutate(prompt: str) -> dict:
        prompts.append(prompt)
        return {
            "content": "# Demo\n\nUse a shortest-path evidence plan before tool calls.\n",
            "rationale": "Low efficiency feedback requires a tighter acquisition strategy.",
        }

    request = OptimizerRequest(
        target=_target(),
        current_content="# Demo\n\nOld guidance.\n",
        target_fingerprint="sha256:old",
        trace_packs=(_trace_pack(),),
        validation_feedback=(
            EvaluationSummary(
                variant_id="candidate-low-efficiency",
                metrics={
                    "score": 69.3,
                    "baseline_score": 70.2,
                    "candidate_score": 69.3,
                    "score_delta": -0.9,
                    "failed_gates": ["score_improvement"],
                    "B2_efficiency": 2.0,
                    "B1_tool_use": 3.0,
                    "A1_groundedness": 4.0,
                },
                dataset_split="validation",
            ),
        ),
        prior_feedback=(
            EvaluationSummary(
                variant_id="candidate-history",
                metrics={
                    "score": 70.3,
                    "baseline_score": 75.4,
                    "candidate_score": 70.3,
                    "score_delta": -5.1,
                    "failed_gates": ["score_improvement"],
                    "B2_efficiency": 2.7,
                },
                dataset_split="historical",
            ),
        ),
        trainable_cases=(EvalCase(case_id="train-1", input="web task"),),
    )

    optimizer = TraceReflectiveLLMMutator(mutate_text=mutate)
    await optimizer.propose(request)

    prompt = prompts[0]
    instruction_text = prompt[: prompt.find("{")]
    assert "efficiency-improvement" in prompt
    assert "score_improvement" in prompt
    assert "B2_efficiency" in prompt
    assert "plan_before_tools" in prompt
    assert "minimize_failed_attempts" in prompt
    assert "avoid_repeated_paths" in prompt
    assert "stop_after_sufficient_evidence" in prompt
    assert "prefer_direct_structured_extraction" in prompt
    assert "shortest viable evidence path" in prompt
    assert "xiaoyuzhou" not in instruction_text.lower()
    assert "podcast" not in instruction_text.lower()
    assert "curl" not in instruction_text.lower()
    assert "cdp" not in instruction_text.lower()


def test_feedback_normalization_requires_stronger_evidence_repair_for_veto_and_manifest_errors() -> None:
    summary = normalize_feedback_summary(
        EvaluationSummary(
            variant_id="candidate-evidence-risk",
            dataset_split="validation",
            metrics={
                "score": 65.25,
                "A1_groundedness": 2.0,
                "veto_triggered": True,
                "evidence_compacted": True,
                "evidence_incomplete": True,
                "evidence_manifest_invalid_entry_count": 2,
                "evidence_manifest_invalid_reasons": [
                    "missing source_id",
                    "missing artifact_path",
                ],
                "failed_gates": [
                    "required_verification",
                    "judge_only_signal",
                ],
            },
        )
    )

    assert summary["metrics"]["evidence_manifest_invalid_entry_count"] == 2
    assert summary["evidence"]["invalid_entry_count"] == 2
    assert summary["evidence"]["invalid_reasons"] == [
        "missing source_id",
        "missing artifact_path",
    ]
    assert summary["evidence"]["veto_triggered"] is True
    assert summary["evidence"]["A1_groundedness"] == 2.0
    assert "manifest_schema_compliance" in summary["required_behaviors"]
    assert "pre_final_veto_check" in summary["required_behaviors"]
    assert "support_every_claim_with_artifact_reference" in summary["required_behaviors"]
    assert "raise_groundedness_before_breadth" in summary["required_behaviors"]


def test_feedback_normalization_penalizes_more_evidence_with_lower_verifiability() -> None:
    summary = normalize_feedback_summary(
        EvaluationSummary(
            variant_id="candidate-scope-regression",
            dataset_split="validation",
            metrics={
                "score": 65.67,
                "baseline_score": 68.0,
                "candidate_score": 65.67,
                "score_delta": -2.33,
                "baseline_evidence_block_count": 22.3,
                "candidate_evidence_block_count": 30.0,
                "evidence_block_count_delta": 7.7,
                "baseline_evidence_incomplete": 0.33,
                "candidate_evidence_incomplete": 0.67,
                "evidence_incomplete_delta": 0.34,
                "baseline_latency_ms": 202_372,
                "candidate_latency_ms": 333_973,
                "latency_ms_delta": 131_601,
                "failed_gates": ["score_improvement"],
            },
        )
    )

    assert summary["metrics"]["evidence_block_count_delta"] == 7.7
    assert summary["metrics"]["evidence_incomplete_delta"] == 0.34
    assert summary["metrics"]["latency_ms_delta"] == 131_601
    assert "reduce_answer_scope_to_verified_claims" in summary["required_behaviors"]
    assert "prefer_fewer_verified_claims_over_broad_synthesis" in summary["required_behaviors"]
    assert "optimize_verifiability_per_evidence_block" in summary["required_behaviors"]
    assert "avoid_collecting_more_evidence_without_verifiability_gain" in summary["required_behaviors"]
    assert "cap_evidence_acquisition_and_summarization_cost" in summary["required_behaviors"]


@pytest.mark.asyncio
async def test_llm_mutator_turns_veto_and_invalid_manifest_feedback_into_generic_strategy() -> None:
    prompts = []

    async def mutate(prompt: str) -> dict:
        prompts.append(prompt)
        return {
            "content": "# Demo\n\nAdd strict artifact evidence validation before final answers.\n",
            "rationale": "The feedback shows invalid manifest entries and veto risk.",
        }

    request = OptimizerRequest(
        target=_target(),
        current_content="# Demo\n\nOld guidance.\n",
        target_fingerprint="sha256:old",
        trace_packs=(_trace_pack(),),
        validation_feedback=(
            EvaluationSummary(
                variant_id="candidate-veto",
                metrics={
                    "score": 65.25,
                    "A1_groundedness": 2.0,
                    "veto_triggered": True,
                    "failed_gates": ["required_verification", "judge_only_signal"],
                    "evidence_compacted": True,
                    "evidence_incomplete": True,
                    "evidence_manifest_invalid_entry_count": 2,
                    "evidence_manifest_invalid_reasons": ["missing source_id"],
                },
                dataset_split="validation",
            ),
        ),
        trainable_cases=(EvalCase(case_id="train-1", input="web task"),),
    )

    optimizer = TraceReflectiveLLMMutator(mutate_text=mutate)
    await optimizer.propose(request)

    prompt = prompts[0]
    instruction_text = prompt[: prompt.find("{")]
    assert "manifest_schema_compliance" in prompt
    assert "pre_final_veto_check" in prompt
    assert "support_every_claim_with_artifact_reference" in prompt
    assert "raise_groundedness_before_breadth" in prompt
    assert "invalid manifest entries" in instruction_text
    assert "veto" in instruction_text
    assert "xiaoyuzhou" not in instruction_text.lower()
    assert "podcast" not in instruction_text.lower()
    assert "curl" not in instruction_text.lower()


@pytest.mark.asyncio
async def test_llm_mutator_turns_scope_and_cost_regression_feedback_into_generic_strategy() -> None:
    prompts = []

    async def mutate(prompt: str) -> dict:
        prompts.append(prompt)
        return {
            "content": "# Demo\n\nPrefer fewer verified claims over broad synthesis.\n",
            "rationale": "Feedback shows lower verifiability despite more evidence.",
        }

    request = OptimizerRequest(
        target=_target(),
        current_content="# Demo\n\nOld guidance.\n",
        target_fingerprint="sha256:old",
        trace_packs=(_trace_pack(),),
        validation_feedback=(
            EvaluationSummary(
                variant_id="candidate-scope-regression",
                metrics={
                    "score": 65.67,
                    "baseline_score": 68.0,
                    "candidate_score": 65.67,
                    "score_delta": -2.33,
                    "baseline_evidence_block_count": 22.3,
                    "candidate_evidence_block_count": 30.0,
                    "evidence_block_count_delta": 7.7,
                    "baseline_evidence_incomplete": 0.33,
                    "candidate_evidence_incomplete": 0.67,
                    "evidence_incomplete_delta": 0.34,
                    "baseline_latency_ms": 202_372,
                    "candidate_latency_ms": 333_973,
                    "latency_ms_delta": 131_601,
                    "failed_gates": ["score_improvement"],
                },
                dataset_split="validation",
            ),
        ),
    )

    optimizer = TraceReflectiveLLMMutator(mutate_text=mutate)
    await optimizer.propose(request)

    prompt = prompts[0]
    instruction_text = prompt[: prompt.find("{")]
    assert "reduce_answer_scope_to_verified_claims" in prompt
    assert "prefer_fewer_verified_claims_over_broad_synthesis" in prompt
    assert "cap_evidence_acquisition_and_summarization_cost" in prompt
    assert "fewer verified claims" in instruction_text
    assert "do not expand answer breadth" in instruction_text
    assert "cap evidence acquisition and summarization cost" in instruction_text
    assert "xiaoyuzhou" not in instruction_text.lower()
    assert "podcast" not in instruction_text.lower()
    assert "curl" not in instruction_text.lower()


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
