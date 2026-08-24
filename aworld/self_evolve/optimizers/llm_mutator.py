from __future__ import annotations

import ast
import hashlib
import inspect
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence

from aworld.self_evolve.candidate_package import (
    candidate_content_semantic_fingerprint,
    candidate_package_fingerprint,
    candidate_package_reference_report,
    candidate_semantic_package_fingerprint,
    validate_candidate_files,
)
from aworld.self_evolve.candidate_generation import (
    CandidateGenerationInfrastructureError,
)
from aworld.self_evolve.candidate_errors import (
    CandidateFailureField,
    CandidateMaterializationCode,
    CandidateMaterializationError,
    CandidateRepresentation,
)
from aworld.self_evolve.concurrency import (
    CandidatePopulationResult,
    SelfEvolveConcurrencyPolicy,
)
from aworld.self_evolve.evolution_context import (
    _repair_feedback_reached_judged_task_output,
    compile_evolution_context,
)
from aworld.self_evolve.feedback import normalize_feedback_summary
from aworld.self_evolve.optimizers.base import (
    CandidateGenerationOutcome,
    CandidateGenerationOutcomeKind,
    CandidateSemanticValidationError,
    OptimizerRequest,
    OptimizerResult,
    declared_addressed_improvement_signal_ids,
    exposed_improvement_signal_ids,
)
from aworld.self_evolve.patch_intent import apply_skill_patch_intent
from aworld.self_evolve.repair_conformance import (
    RepairConformanceContract,
    compile_repair_conformance_contract,
    evaluate_candidate_source_conformance,
)
from aworld.self_evolve.replay_capability import (
    REPLAY_RESPONSE_INDEX_CONSUMER,
    recorded_response_index_source_behavior_proof,
)
from aworld.self_evolve.sanitization import sanitize_text
from aworld.self_evolve.types import CandidateFileDelta, CandidateVariant, OptimizerLineage
from aworld.skills.structure import (
    build_skill_structural_edit_intent,
    validate_skill_markdown_structure,
)
from aworld.skills.structure_types import SkillStructuralEditIntent


MutateTextCallable = Callable[[str], Any]
CandidatePopulationCallable = Callable[..., Awaitable[CandidatePopulationResult]]


class TraceReflectiveLLMMutator:
    optimizer_name = "trace-reflective-llm-mutator"
    optimizer_version = "0"

    def __init__(
        self,
        *,
        mutate_text: MutateTextCallable,
        population_callable: CandidatePopulationCallable | None = None,
        concurrency_policy: SelfEvolveConcurrencyPolicy | None = None,
    ) -> None:
        self.mutate_text = mutate_text
        self.population_callable = population_callable
        self.concurrency_policy = concurrency_policy or SelfEvolveConcurrencyPolicy()

    async def propose(self, request: OptimizerRequest) -> OptimizerResult:
        if not _has_lesson_backed_delta_signal(request):
            return OptimizerResult(
                candidates=(),
                lineage=(),
                diagnostics={
                    "filtered_noop_candidates": 0,
                    "filtered_high_baseline_regression_candidates": 0,
                    "filtered_duplicate_candidates": 0,
                    "candidate_strategies": (),
                    "no_op_recommended": True,
                    "no_op_reason": "no_lesson_backed_safe_delta",
                },
            )
        candidates: list[CandidateVariant] = []
        lineage: list[OptimizerLineage] = []
        filtered_noop_count = 0
        filtered_high_baseline_regression_count = 0
        high_baseline_policy_risk_count = 0
        filtered_duplicate_count = 0
        filtered_invalid_patch_count = 0
        repaired_transport_completion_violation_count = 0
        preserved_existing_replay_file_delta_count = 0
        seen_content_fingerprints: set[str] = set()
        candidate_strategy_records: list[dict[str, Any]] = []
        generation_outcomes: list[CandidateGenerationOutcome] = []
        private_repair_contracts: dict[str, RepairConformanceContract] = {}
        candidate_generation_failure: dict[str, str] | None = None
        candidate_protocol_invalid_count = 0
        candidate_materialization_failures: list[dict[str, object]] = []
        candidate_outputs: list[tuple[int, Any]] = []
        population_diagnostics: dict[str, Any]
        population_started_at = time.monotonic()

        if self.population_callable is not None:
            prompts = tuple(
                _build_mutation_prompt(request, candidate_index=index)
                for index in range(request.max_candidates)
            )
            population = await _run_candidate_population(
                self.population_callable,
                prompts=prompts,
                max_concurrency=self.concurrency_policy.effective_limit(
                    "candidate_generation",
                    item_count=len(prompts),
                ),
                validate_output=lambda index, output: (
                    _validate_mutator_output_context(
                        output,
                        request=request,
                        candidate_index=index,
                    )
                ),
            )
            population_diagnostics = dict(population.diagnostics)
            for slot in population.slots:
                if slot.status == "succeeded":
                    candidate_outputs.append((slot.index, slot.output))
                elif slot.status == "failed" and candidate_generation_failure is None:
                    candidate_generation_failure = dict(slot.failure or {})
                    generation_outcomes.append(
                        CandidateGenerationOutcome(
                            candidate_index=slot.index,
                            kind=(
                                CandidateGenerationOutcomeKind.INFRASTRUCTURE_FAILED
                            ),
                            repairable=False,
                            reason_codes=(
                                str(
                                    candidate_generation_failure.get("code")
                                    or "candidate_generation_infrastructure_error"
                                ),
                            ),
                            active_frontier_key=_active_frontier_key(
                                request,
                                slot.index,
                            ),
                        )
                    )
                elif slot.status == "protocol_invalid":
                    failure = dict(slot.failure or {})
                    if (
                        failure.get("stage")
                        == "candidate_semantic_validation"
                    ):
                        filtered_invalid_patch_count += 1
                        candidate_materialization_failures.append(
                            {
                                **failure,
                                "candidate_index": slot.index,
                                "representation": (
                                    failure.get("representation")
                                    or CandidateRepresentation.CANDIDATE_PACKAGE.value
                                ),
                            }
                        )
                        generation_outcomes.append(
                            CandidateGenerationOutcome(
                                candidate_index=slot.index,
                                kind=(
                                    CandidateGenerationOutcomeKind.MATERIALIZATION_FAILED
                                ),
                                repairable=failure.get("repairable") is not False,
                                reason_codes=(
                                    str(
                                        failure.get("code")
                                        or "candidate_materialization_invalid"
                                    ),
                                ),
                                active_frontier_key=_active_frontier_key(
                                    request,
                                    slot.index,
                                ),
                            )
                        )
                    else:
                        candidate_protocol_invalid_count += 1
                        generation_outcomes.append(
                            CandidateGenerationOutcome(
                                candidate_index=slot.index,
                                kind=CandidateGenerationOutcomeKind.PROTOCOL_INVALID,
                                repairable=failure.get("repairable") is not False,
                                reason_codes=(
                                    str(
                                        failure.get("code")
                                        or "candidate_protocol_invalid"
                                    ),
                                ),
                                active_frontier_key=_active_frontier_key(
                                    request,
                                    slot.index,
                                ),
                            )
                        )
        else:
            statuses = ["discarded"] * request.max_candidates
            failure_cutoff: int | None = None
            for index in range(request.max_candidates):
                prompt = _build_mutation_prompt(request, candidate_index=index)
                try:
                    output = self.mutate_text(prompt)
                    if inspect.isawaitable(output):
                        output = await output
                except CandidateGenerationInfrastructureError as exc:
                    candidate_generation_failure = exc.to_diagnostic()
                    generation_outcomes.append(
                        CandidateGenerationOutcome(
                            candidate_index=index,
                            kind=(
                                CandidateGenerationOutcomeKind.INFRASTRUCTURE_FAILED
                            ),
                            repairable=False,
                            reason_codes=(
                                str(
                                    candidate_generation_failure.get("code")
                                    or "candidate_generation_infrastructure_error"
                                ),
                            ),
                            active_frontier_key=_active_frontier_key(
                                request,
                                index,
                            ),
                        )
                    )
                    statuses[index] = "failed"
                    failure_cutoff = index
                    break
                statuses[index] = "succeeded"
                candidate_outputs.append((index, output))
            population_diagnostics = {
                "mode": "custom_serial",
                "item_count": request.max_candidates,
                "configured_concurrency": 1,
                "effective_concurrency": min(1, request.max_candidates),
                "max_observed_concurrency": min(
                    1,
                    len(candidate_outputs) + (1 if failure_cutoff is not None else 0),
                ),
                "failure_cutoff_index": failure_cutoff,
                "statuses": statuses,
                "repair_count": 0,
                "resource_serialized_count": 0,
                "queue_wait_seconds": 0.0,
                "execution_seconds": time.monotonic() - population_started_at,
                "elapsed_seconds": time.monotonic() - population_started_at,
        }

        for index, output in candidate_outputs:
            try:
                content, rationale, materialization, files = _materialize_mutator_output(
                    output,
                    request=request,
                    candidate_index=index,
                )
                addressed_signal_ids = (
                    declared_addressed_improvement_signal_ids(
                        request,
                        output,
                    )
                )
                files, inherited_file_count = _overlay_repair_focus_files(
                    request,
                    candidate_index=index,
                    candidate_files=files,
                )
                if inherited_file_count:
                    materialization = f"{materialization}+repair_focus_overlay"
                files, preserved_replay_file_count = (
                    _preserve_existing_replay_package_for_target_delta(
                        request,
                        candidate_index=index,
                        candidate_files=files,
                    )
                )
                preserved_existing_replay_file_delta_count += (
                    preserved_replay_file_count
                )
                repair_context = (
                    request.evolution_context
                    or compile_evolution_context(request)
                )
                repair_focus = repair_context.repair_focus_for_candidate(
                    candidate_index=index
                )
                _validate_judge_repair_target_delta(
                    request,
                    repair_focus=repair_focus,
                    candidate_content=content,
                )
                _validate_prerequisite_composition_target_delta(
                    request,
                    repair_focus=repair_focus,
                    candidate_content=content,
                )
                _validate_focused_repair_mutation_scope(
                    request,
                    repair_focus=repair_focus,
                    candidate_content=content,
                    candidate_files=files,
                )
                if (
                    isinstance(repair_focus, Mapping)
                    and _repair_feedback_is_prerequisite_composition(
                        repair_focus
                    )
                ):
                    rationale = _verified_prerequisite_composition_rationale(
                        repair_focus
                    )
                if _violates_transport_completion_invariant(content):
                    content = _append_transport_completion_invariant(content)
                    repaired_transport_completion_violation_count += 1
                structural_edit_intent = _candidate_structural_edit_intent(
                    output,
                    base_content=request.current_content,
                    candidate_content=content,
                )
            except ValueError as exc:
                filtered_invalid_patch_count += 1
                semantic_error = _candidate_semantic_error(
                    exc,
                    output=output,
                    request=request,
                )
                diagnostic = semantic_error.to_diagnostic()
                candidate_materialization_failures.append(
                    {
                        **diagnostic,
                        "candidate_index": index,
                        "representation": (
                            diagnostic.get("representation")
                            or _candidate_output_representation(output).value
                        ),
                        "reason": sanitize_text(str(exc), max_chars=240),
                    }
                )
                generation_outcomes.append(
                    CandidateGenerationOutcome(
                        candidate_index=index,
                        kind=CandidateGenerationOutcomeKind.MATERIALIZATION_FAILED,
                        repairable=diagnostic.get("repairable") is not False,
                        reason_codes=(str(diagnostic.get("code") or "invalid"),),
                        active_frontier_key=_active_frontier_key(request, index),
                        strategy_id=_candidate_strategy_id(request, index),
                    )
                )
                continue
            strategy_record = _candidate_strategy_record(
                request,
                candidate_index=index,
                addressed_signal_ids=addressed_signal_ids,
            )
            parent_candidate_ids = _repair_focus_parent_candidate_ids(
                request,
                candidate_index=index,
            )
            if content == request.current_content and not files:
                filtered_noop_count += 1
                generation_outcomes.append(
                    CandidateGenerationOutcome(
                        candidate_index=index,
                        kind=CandidateGenerationOutcomeKind.NOOP_FILTERED,
                        repairable=True,
                        reason_codes=("candidate_matches_current_package",),
                        active_frontier_key=_active_frontier_key(request, index),
                        strategy_id=str(strategy_record["strategy_id"]),
                    )
                )
                continue
            candidate = CandidateVariant(
                candidate_id="pending",
                target=request.target,
                content=content,
                rationale=rationale,
                parent_candidate_ids=parent_candidate_ids,
                target_fingerprint=request.target_fingerprint,
                files=files,
                structural_edit_intent=structural_edit_intent,
            )
            content_fingerprint = candidate_package_fingerprint(candidate)
            semantic_package_fingerprint = (
                candidate_semantic_package_fingerprint(candidate)
            )
            if semantic_package_fingerprint in seen_content_fingerprints:
                filtered_duplicate_count += 1
                generation_outcomes.append(
                    CandidateGenerationOutcome(
                        candidate_index=index,
                        kind=CandidateGenerationOutcomeKind.DUPLICATE_FILTERED,
                        candidate_fingerprint=content_fingerprint,
                        semantic_fingerprint=semantic_package_fingerprint,
                        repairable=True,
                        reason_codes=("duplicate_candidate_semantics",),
                        active_frontier_key=_active_frontier_key(request, index),
                        strategy_id=str(strategy_record["strategy_id"]),
                    )
                )
                continue
            candidate_id = _candidate_id(
                request,
                content,
                files=files,
                index=index,
            )
            candidate = CandidateVariant(
                candidate_id=candidate_id,
                target=request.target,
                content=content,
                rationale=rationale,
                parent_candidate_ids=parent_candidate_ids,
                target_fingerprint=request.target_fingerprint,
                files=files,
                structural_edit_intent=structural_edit_intent,
            )
            policy_assessment = _high_baseline_policy_assessment(
                content,
                current_content=request.current_content,
                request=request,
                candidate_index=index,
            )
            if policy_assessment is not None:
                policy_payload = policy_assessment.to_dict()
                strategy_record["policy_assessment"] = policy_payload
                if policy_assessment.enforcement == "hard":
                    filtered_high_baseline_regression_count += 1
                    candidate_strategy_records.append(
                        {
                            "candidate_id": candidate_id,
                            "materialization": materialization,
                            "admission_status": "policy_filtered",
                            **strategy_record,
                        }
                    )
                    generation_outcomes.append(
                        CandidateGenerationOutcome(
                            candidate_index=index,
                            kind=CandidateGenerationOutcomeKind.POLICY_FILTERED,
                            candidate_id=candidate_id,
                            candidate_fingerprint=content_fingerprint,
                            semantic_fingerprint=semantic_package_fingerprint,
                            policy_id=policy_assessment.policy_id,
                            enforcement=policy_assessment.enforcement,
                            repairable=True,
                            reason_codes=policy_assessment.reason_codes,
                            constraint_ids=policy_assessment.constraint_ids,
                            active_frontier_key=_active_frontier_key(
                                request,
                                index,
                            ),
                            affected_case_ids=_policy_affected_case_ids(
                                request,
                                candidate_index=index,
                            ),
                            strategy_id=str(strategy_record["strategy_id"]),
                        )
                    )
                    continue
                high_baseline_policy_risk_count += 1
            seen_content_fingerprints.add(semantic_package_fingerprint)
            candidates.append(candidate)
            context = request.evolution_context or compile_evolution_context(request)
            repair_focus = context.repair_focus_for_candidate(
                candidate_index=index
            )
            private_contract = (
                None
                if (
                    isinstance(repair_focus, Mapping)
                    and (
                        _repair_feedback_reached_judged_task_output(repair_focus)
                        or _repair_feedback_is_prerequisite_composition(
                            repair_focus
                        )
                    )
                )
                else compile_repair_conformance_contract(repair_focus)
            )
            if private_contract is not None:
                private_repair_contracts[candidate_id] = private_contract
            candidate_strategy_records.append(
                {
                    "candidate_id": candidate_id,
                    "materialization": materialization,
                    "admission_status": "admitted",
                    "structural_edit_authorization": (
                        candidate.structural_edit_intent.authorization
                        if candidate.structural_edit_intent is not None
                        else None
                    ),
                    **strategy_record,
                }
            )
            generation_outcomes.append(
                CandidateGenerationOutcome(
                    candidate_index=index,
                    kind=CandidateGenerationOutcomeKind.ADMITTED,
                    candidate_id=candidate_id,
                    candidate_fingerprint=content_fingerprint,
                    semantic_fingerprint=semantic_package_fingerprint,
                    enforcement=(
                        policy_assessment.enforcement
                        if policy_assessment is not None
                        else None
                    ),
                    reason_codes=(
                        policy_assessment.reason_codes
                        if policy_assessment is not None
                        else ()
                    ),
                    constraint_ids=(
                        policy_assessment.constraint_ids
                        if policy_assessment is not None
                        else ()
                    ),
                    active_frontier_key=_active_frontier_key(request, index),
                    affected_case_ids=_policy_affected_case_ids(
                        request,
                        candidate_index=index,
                    ),
                    strategy_id=str(strategy_record["strategy_id"]),
                )
            )
            lineage.append(
                OptimizerLineage(
                    candidate_id=candidate_id,
                    optimizer_name=self.optimizer_name,
                    optimizer_version=self.optimizer_version,
                    parent_candidate_ids=parent_candidate_ids,
                    trainable_case_ids=tuple(case.case_id for case in request.trainable_cases),
                    content_fingerprint=content_fingerprint,
                    semantic_fingerprint=candidate_content_semantic_fingerprint(
                        content
                    ),
                    lesson_set_fingerprint=_lesson_set_fingerprint(request),
                    addressed_lesson_ids=_addressed_lesson_ids(request),
                    improvement_signal_set_fingerprint=(
                        request.improvement_signal_set_fingerprint
                    ),
                    exposed_improvement_signal_ids=(
                        exposed_improvement_signal_ids(request)
                    ),
                    addressed_improvement_signal_ids=addressed_signal_ids,
                    rationale=rationale,
                )
            )

        diagnostics: dict[str, object] = {
            "filtered_noop_candidates": filtered_noop_count,
            "filtered_high_baseline_regression_candidates": (
                filtered_high_baseline_regression_count
            ),
            "high_baseline_policy_risk_candidates": (
                high_baseline_policy_risk_count
            ),
            "filtered_duplicate_candidates": filtered_duplicate_count,
            "filtered_invalid_patch_candidates": filtered_invalid_patch_count,
            "repaired_transport_completion_violation_candidates": (
                repaired_transport_completion_violation_count
            ),
            "preserved_existing_replay_file_delta_count": (
                preserved_existing_replay_file_delta_count
            ),
            "candidate_strategies": candidate_strategy_records,
            "candidate_population_execution": population_diagnostics,
            "candidate_protocol_invalid_count": candidate_protocol_invalid_count,
            "candidate_materialization_failures": (
                candidate_materialization_failures
            ),
            "candidate_generation_outcomes": [
                outcome.to_dict() for outcome in generation_outcomes
            ],
        }
        if candidate_generation_failure is not None:
            diagnostics["candidate_generation_failure"] = candidate_generation_failure

        return OptimizerResult(
            candidates=tuple(candidates),
            lineage=tuple(lineage),
            generation_outcomes=tuple(generation_outcomes),
            diagnostics=diagnostics,
            private_context=private_repair_contracts,
        )


async def _run_candidate_population(
    population_callable: CandidatePopulationCallable,
    *,
    prompts: Sequence[str],
    max_concurrency: int,
    validate_output: Callable[
        [int, Mapping[str, Any]],
        Mapping[str, Any],
    ],
) -> CandidatePopulationResult:
    """Pass contextual validation when supported, preserving legacy callables."""

    try:
        parameters = inspect.signature(population_callable).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    accepts_contextual_validation = any(
        parameter.name == "validate_output"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
    if accepts_contextual_validation:
        return await population_callable(
            prompts,
            max_concurrency,
            validate_output=validate_output,
        )
    return await population_callable(prompts, max_concurrency)


def _build_mutation_prompt(request: OptimizerRequest, *, candidate_index: int) -> str:
    context = request.evolution_context or compile_evolution_context(request)
    payload = context.to_prompt_payload(candidate_index=candidate_index)
    if isinstance(payload.get("repair_focus"), Mapping):
        return _focused_repair_prompt_instructions(payload) + json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        )
    if payload.get("repair_context_mode") == "generation_policy_delta":
        return (
            "Repair the typed generation-policy violation in this bounded "
            "EvolutionContext. Treat current_content as the authoritative base, "
            "satisfy every candidate_validation_diagnostic constraint_id, and use "
            "patch_intent for the smallest structural delta. A policy reason is a "
            "source-shape constraint, not permission to copy historical candidate "
            "content or broaden the task behavior. Preserve unrelated target behavior "
            "and return exactly one expected_output object.\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )
    return (
        "Generate one candidate package from this bounded EvolutionContext. Follow "
        "population_strategy, required_behaviors, preserved_behaviors, "
        "capability_contracts, acceptance_constraints, and expected_output literally. "
        "Prefer the smallest reusable behavior delta; use patch_intent for a bounded "
        "change to large target content, and never hard-code task ids, case ids, original "
        "endpoints, environment paths, fixture hashes, or diagnostic previews. "
        "For reusable large-output handling, require unknown-size responses to be redirected "
        "to an artifact before inspection and derive only explicit byte-bounded excerpts or "
        "selected structured fields from that file. A line-count limit such as head -N is "
        "not a byte bound because a response may contain one very large line. "
        "Separate transport completion from task completion for every candidate. A successful "
        "handshake, HTTP status, structured envelope, metadata record, or tool-execution "
        "summary is only a delivery signal. Persist the first usable response immediately, "
        "then verify that its payload directly supports the claims requested by the user. "
        "Stop only when that semantic check passes; otherwise make exactly one materially "
        "different bounded artifact-backed attempt, then return supported claims or an "
        "explicit insufficiency without another tool call. Before finalizing, omit every "
        "unsupported factual, configuration, bibliographic, quantitative, symbolic, or "
        "quoted claim; do not expand captured evidence with remembered details. Never encode "
        "a blanket first-response-means-complete rule or a case-specific endpoint or prompt. "
        "Keep replay schema layers distinct: replay/capability.json protocol is exactly "
        "aworld.replay.subprocess.v1; its handles are request requirement kinds such as "
        "http_resource, never runtime_required or skill_runtime. The compiler writes "
        "aworld.replay.capability_result.v1, where a candidate-owned runtime is declared "
        "with service transport skill_runtime and a runtime_entrypoint listed by the "
        "manifest runtime_files. Do not invent supported_requirement_kinds, "
        "runtime_required, fixture_resolver, or service-transport values in the manifest. "
        "The framework creates only the compiler output root. The compiler owns every "
        "declared subdirectory and must create parents such as output/fixtures before "
        "copying or writing files; it must not assume those directories already exist. "
        "Evidence inputs may be immutable: copy fixture bytes with shutil.copyfile or "
        "an explicit read/write, never shutil.copy2, copymode, or copied source metadata. "
        "Use a requirement-qualified destination or deduplicate before writing so two "
        "requirements selecting the same source never overwrite a read-only fixture. "
        "The compiler is a deterministic artifact transform and runs without network or "
        "loopback access: never bind/connect sockets, select a live port, launch the runtime, "
        "or probe readiness during compile. Declare the runtime in result.json; the framework "
        "starts it later with an allocated port. Each request requirement's evidence_refs is "
        "an array of string keys; resolve each key through request.evidence_derivations, whose "
        "values are arrays of source objects. Never call mapping methods on an evidence_ref. "
        "For a skill_runtime, AWORLD_REPLAY_RESPONSE_INDEX is a filesystem path supplied "
        "by the framework to a JSON sidecar with schema "
        "{schema_version, operations, records}; it is not an integer, inline response, "
        "fixture selector, or compiler-owned output. Open that path, iterate the records "
        "array, derive the incoming operation, and select its first record whose non_empty "
        "and protocol_eligible fields are true (or advance a deterministic per-operation "
        "cursor); transport_ready records are ordered first. For backward-compatible "
        "sidecars without these fields, use the first non_empty record whose value can be "
        "recursively decoded or projected into a bounded response. Then project "
        "record['value'] into the protocol result. Do not rescan the raw fixture as a "
        "substitute for consuming the sidecar. Resolve record['payload_path'] from "
        "AWORLD_REPLAY_FIXTURE_PATH only when value is absent. Index fields such as "
        "gateway_key, operation, payload_path, shape, and non_empty are metadata and must "
        "never become task output. Preserve the decoded recorded container and its response "
        "shape when it fits the bounded transport; for an oversized record, return a "
        "deterministic bounded projection from that same container which retains at least "
        "two non-empty scalar descendants when available. Keep the serialized HTTP response "
        "below 48 KiB so the 64 KiB protocol reader can validate the whole projection; do "
        "not merely send an oversized body that will be truncated. Use a scalar descendant "
        "only as response_contains. The same selected leaf may be reused by multiple probes. "
        "Correlate request ids, routing fields, operations, "
        "and bounded parameters; a global token, empty schema, readiness-only handler, or "
        "unused parameter read is non-conforming. Discovery probes assert protocol structure; "
        "a paired data-plane probe carries fixture-derived content. An endpoint replacement "
        "hands the task the service base URL: keep readiness on a distinct control-plane "
        "path, and make the base task entry return recorded evidence or a protocol-standard "
        "discovery response whose advertised task-plane route is fully implemented and "
        "fixture-backed. Do not make the base task entry a readiness-only response. "
        "Treat any previous "
        "expected_preview as diagnostic evidence rather than a value to hard-code. "
        "When validation_feedback contains repair_candidate_package, edit that bounded "
        "source as a delta and preserve its verified behavior. "
        "When validation_feedback contains a typed recovery_trace, preserve members and "
        "repetitions with positive recovery_delta while repairing unrecovered members. "
        "Treat failed_progress_exceeded_success as evidence of post-checkpoint overrun: "
        "bound further attempts or switch to one materially different strategy instead "
        "of repeating the failed path. Treat failure_loop_detected or the corresponding "
        "typed guidance as a requirement for an explicit attempt bound and a materially "
        "different fallback, not another equivalent retry. Never key behavior to member "
        "identities or copy "
        "tool names as case-specific rules. "
        "When validation_feedback reports duplicate_semantic_lesson, produce a materially "
        "different complete candidate package by changing reusable target behavior or "
        "candidate-owned files. Renaming, reformatting, changing rationale, or copying the "
        "same files does not satisfy that typed repair frontier. Apply the distinction "
        "generically across every trajectory member represented by the active capability "
        "and verification contracts. "
        "Treat evidence_repair_constraints as the authoritative evidence frontier: "
        "deduplicate by constraint_identity_digest, honor owner and source_layer, and "
        "implement the declared required_action. Do not mutate candidate behavior for a "
        "framework- or infrastructure-owned constraint, and do not infer policy from "
        "free-form evidence issue wording. "
        "Treat replay manifest capability_id as immutable package identity. A compiler "
        "request carries that authoritative identity in request.capability_id, and the "
        "compiler result must copy it exactly; never shorten it, derive it from the "
        "skill name, or emit a template placeholder. For an enum schema_field_constraint "
        "with one expected value, copy its single expected value exactly into the declared "
        "field at the specified schema_layer. "
        "Keep reusable examples schema-neutral: use role placeholders such as "
        "<CLAIM>, <ARTIFACT_PATH>, and <OFFSET> instead of copying proper nouns, "
        "resource names, claim text, filenames, URLs, or identifiers from trajectory "
        "evidence. "
        "Replay files must accompany a reusable target behavior delta, not replace it. "
        "Treat SKILL.md and every candidate-owned file as one atomic release package. "
        "Include every added or changed package file in files. Every concrete replay/... "
        "path named by content or patch_intent must already exist in "
        "target_package_inventory or be supplied as an upsert in files; never describe "
        "a file that is absent from the candidate package. A files[*].content value "
        "starts with the file's first byte: never place a Markdown filename heading "
        "such as '# replay/capability.json' inside JSON or source file content. "
        "Every Markdown fenced block in content or patch_intent must be balanced inside the "
        "same field. Do not duplicate or partially splice an existing section. Return the "
        "value of expected_output as exactly one JSON object, without a wrapper; "
        "use at most one of content or patch_intent, and omit both only when candidate-owned "
        "files implement the reusable delta.\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def _focused_repair_prompt_instructions(
    payload: Mapping[str, object],
) -> str:
    contract = payload.get("repair_conformance")
    contract_mapping = contract if isinstance(contract, Mapping) else {}
    failure_codes = {
        str(value)
        for value in contract_mapping.get("failure_codes", ())
        if isinstance(value, str)
    }
    required_runtime_transitions = {
        str(value)
        for value in contract_mapping.get("required_runtime_transitions", ())
        if isinstance(value, str)
    }
    raw_fixture_probe_constraints = contract_mapping.get(
        "fixture_probe_constraints",
        (),
    )
    fixture_probe_constraints = (
        tuple(
            item
            for item in raw_fixture_probe_constraints
            if isinstance(item, Mapping)
        )
        if isinstance(raw_fixture_probe_constraints, (list, tuple))
        else ()
    )
    requires_fixture_reconstruction = bool(
        contract_mapping.get("requires_compiler_fixture_reconstruction") is True
        or contract_mapping.get("requires_fixture_derived_probe") is True
        or fixture_probe_constraints
    )
    artifact_lifecycle_constraint = contract_mapping.get(
        "artifact_lifecycle_constraint"
    )
    raw_schema_field_constraints = contract_mapping.get(
        "schema_field_constraints",
        (),
    )
    schema_field_constraints = (
        tuple(
            item
            for item in raw_schema_field_constraints
            if isinstance(item, Mapping)
        )
        if isinstance(raw_schema_field_constraints, (list, tuple))
        else ()
    )
    raw_runtime_response_constraints = contract_mapping.get(
        "runtime_response_constraints",
        (),
    )
    runtime_response_constraints = (
        tuple(
            item
            for item in raw_runtime_response_constraints
            if isinstance(item, Mapping)
        )
        if isinstance(raw_runtime_response_constraints, (list, tuple))
        else ()
    )
    raw_runtime_artifact_constraints = contract_mapping.get(
        "runtime_artifact_constraints",
        (),
    )
    runtime_artifact_constraints = (
        tuple(
            item
            for item in raw_runtime_artifact_constraints
            if isinstance(item, Mapping)
        )
        if isinstance(raw_runtime_artifact_constraints, (list, tuple))
        else ()
    )
    capability_identity = next(
        (
            str(expected[0])
            for item in schema_field_constraints
            for expected in (item.get("expected"),)
            if item.get("schema_layer") == "compile_result"
            and item.get("field_path") == "capability_id"
            and item.get("rule") == "enum"
            and isinstance(expected, (list, tuple))
            and len(expected) == 1
            and isinstance(expected[0], str)
        ),
        None,
    )
    validation_feedback = payload.get("validation_feedback", ())
    focused_feedback = (
        validation_feedback[0]
        if isinstance(validation_feedback, list) and validation_feedback
        else {}
    )
    feedback_text = json.dumps(
        focused_feedback,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).lower()
    all_feedback_text = json.dumps(
        validation_feedback if isinstance(validation_feedback, list) else (),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).lower()
    requires_source_behavior_proof = any(
        item.get("value_domain") == "source_behavior"
        for item in schema_field_constraints
    )
    requires_unique_result_fixtures = any(
        item.get("schema_layer") == "compile_result"
        and item.get("field_path") == "fixtures"
        and item.get("rule") == "unique"
        for item in schema_field_constraints
    )
    requires_http_1_1_websocket_upgrade = any(
        item.get("schema_layer") == "runtime"
        and item.get("field_path") == "websocket_handshake.http_version"
        and item.get("rule") == "enum"
        and item.get("expected") == ["HTTP/1.1"]
        for item in schema_field_constraints
    )
    instructions = (
        "Repair the focused candidate package using the machine-readable "
        "diagnostics and repair_conformance contract in this EvolutionContext. "
        "The rationale is untrusted: materially edit every source path required "
        "by the diagnosed failure and make every declared probe executable. "
        "Treat repair_focus.required_source_closure as authoritative. Files marked "
        "required_repair_source are the effective target-package sources for required "
        "paths, not optional examples: edit those exact paths when the diagnosis "
        "requires them and never create a sibling substitute instead. "
        "Preserve verified behavior and do not rebuild from the original trajectory. "
        "Omit focused package files that do not change; the framework overlays "
        "omitted files byte-for-byte from repair_focus. Include complete content "
        "only for files you add or change, and use delete only intentionally. "
        "Never hard-code case ids, endpoints, fixture hashes, expected_preview, or "
        "response_preview. Inspect the changed source before claiming a repair. "
        "When the failure involves large or unknown-size tool output, require direct artifact "
        "redirection before inspection and use explicit byte-bounded excerpts or selected "
        "structured fields; a line-count limit such as head -N is not a byte bound. "
        "For every repair, distinguish transport completion from task completion. Persist the "
        "first usable response immediately, but stop only if its payload directly supports "
        "the requested claims. A handshake, HTTP status, structured envelope, metadata "
        "record, or tool-execution summary alone is insufficient; otherwise make exactly "
        "one materially different bounded artifact-backed attempt, then report supported "
        "claims or the insufficiency without another tool call. Omit every unsupported "
        "factual, configuration, bibliographic, quantitative, symbolic, or quoted claim; "
        "do not expand captured evidence with remembered details. "
        "Never add a blanket first-response-means-complete rule or case-specific behavior. "
        "For an enum schema_field_constraint with one expected value, copy its single "
        "expected value exactly into that field at the declared schema_layer. Treat the "
        "manifest capability identity as immutable and never infer it from the target "
        "name. "
        "Keep reusable examples schema-neutral: use role placeholders such as "
        "<CLAIM>, <ARTIFACT_PATH>, and <OFFSET> instead of copying proper nouns, "
        "resource names, claim text, filenames, URLs, or identifiers from trajectory "
        "evidence. "
    )
    if capability_identity is not None:
        instructions += (
            "The authoritative manifest capability identity for this repair is "
            f"{json.dumps(capability_identity, ensure_ascii=True)}. Set the "
            "compile-result capability_id to request['capability_id'], which must equal "
            "exactly that value, and preserve the binding across later repair iterations. "
        )
    if requires_source_behavior_proof or (
        "source_behavior_proof_failed" in feedback_text
    ):
        instructions += (
            "For every source_behavior schema constraint, satisfy the typed "
            "operation_status proof in the actual submitted source. Repair each "
            "false operation and inspect missing_operations before finalizing. "
            "The bounded analyzer follows local assignments and explicit function "
            "parameters; it intentionally does not infer data flow through mutable "
            "class attributes, instance attributes, globals, or container state. "
            "When unsupported_boundaries are reported, carry the environment-derived "
            "value through local variables or explicit parameters until the required "
            "reader and projection operations occur. Do not claim a direct read in "
            "the rationale unless that data-flow edge exists in the source. Use this "
            "supported topology inside the actual response-producing lexical scope: "
            "`path = os.getenv(\"AWORLD_REPLAY_RESPONSE_INDEX\")`; "
            "`with open(path, encoding=\"utf-8\") as stream: index = "
            "json.load(stream)`; `records = index[\"records\"]`; then project "
            "`record[\"value\"]`. The environment read and file open must be in the "
            "same function. Do not put either operation behind a returning helper, "
            "even when the helper takes explicit parameters, and do not rely on helper "
            "names to express the proof. "
        )
    if isinstance(artifact_lifecycle_constraint, Mapping):
        instructions += (
            "This repair has a typed artifact_lifecycle_constraint. Change reusable "
            "target behavior so evidence collection has an explicit bounded attempt "
            "path, persists the first valid artifact and manifest entry immediately, "
            "reuses that artifact instead of creating retry files, and returns the "
            "final answer without another collection tool call once evidence is ready. "
            "The candidate must demonstrate these counters in representative "
            "screening; a rationale or documentation-only claim cannot satisfy "
            "admission. Honor max_artifact_files="
            f"{artifact_lifecycle_constraint.get('max_artifact_files')}, "
            "max_artifact_bytes="
            f"{artifact_lifecycle_constraint.get('max_artifact_bytes')}, "
            "and max_collection_tool_calls="
            f"{artifact_lifecycle_constraint.get('max_collection_tool_calls')}. "
        )
    recovery_trace = (
        focused_feedback.get("recovery_trace")
        if isinstance(focused_feedback, Mapping)
        else None
    )
    if isinstance(recovery_trace, Mapping):
        instructions += (
            "This repair has a typed recovery_trace. Keep the focused candidate's "
            "positive recovery deltas and successful structural checkpoint, then make "
            "the unrecovered or unstable branches converge within a bounded attempt "
            "budget. A timeout path that progresses beyond a successful checkpoint is "
            "an overrun signal, not evidence that more identical exploration is needed. "
            "A detected repeated-failure loop must gain an explicit attempt bound and "
            "one structurally different fallback before finalizing or reporting bounded "
            "insufficiency. "
        )
    constraint_recovery_trace = (
        focused_feedback.get("constraint_recovery_trace")
        if isinstance(focused_feedback, Mapping)
        else None
    )
    if isinstance(constraint_recovery_trace, Mapping):
        instructions += (
            "This repair also has a typed constraint_recovery_trace. Preserve every "
            "constraint whose status is recovered as a last-good checkpoint. Restore "
            "any regressed constraint before adding new behavior. When an active "
            "constraint has violation_attempt_count greater than one, do not repeat "
            "the same source shape with renamed helpers or a rationale-only change; "
            "switch to a materially different implementation of the declared typed "
            "operations and verify the actual source data flow before finalizing. "
            "Constraint identities are hashes and must never become runtime branches. "
        )
    raw_evidence_constraints = (
        focused_feedback.get("evidence_repair_constraints")
        if isinstance(focused_feedback, Mapping)
        else None
    )
    candidate_evidence_constraints = (
        [
            item
            for item in raw_evidence_constraints
            if isinstance(item, Mapping)
            and item.get("owner") == "candidate"
        ]
        if isinstance(raw_evidence_constraints, list)
        else []
    )
    if candidate_evidence_constraints:
        instructions += (
            "This repair has candidate-owned typed evidence_repair_constraints. "
            "Apply every distinct required_action at its declared source_layer and "
            "subject_kind, preserving occurrence counts only as prioritization evidence. "
            "Do not copy claim text into reusable instructions, do not branch on constraint "
            "identity hashes, and do not reinterpret evaluator prose as an additional "
            "constraint. "
        )
        required_evidence_actions = {
            str(item.get("required_action") or "")
            for item in candidate_evidence_constraints
        }
        if "repair_artifact_reference" in required_evidence_actions:
            instructions += (
                "For repair_artifact_reference, make the reusable target require "
                "that every file presented as final-answer evidence resolves to a "
                "valid canonical manifest or bundle entry. A file merely existing "
                "in the artifact directory is insufficient: register it as a "
                "bounded evidence source or omit it from the final evidence ledger. "
            )
    repair_support = payload.get("repair_support")
    if isinstance(repair_support, Mapping):
        instructions += (
            "repair_support is a distinct source-omitted repair frontier. When both "
            "repair_focus and repair_support contain judge-stage metrics, treat their "
            "passed and failed gates as complementary checkpoints: preserve the focused "
            "package's verified behavior while satisfying the union of typed constraints "
            "and required behaviors exposed by both frontiers. Do not infer or recreate "
            "the omitted sibling source, average incompatible outputs, or trade a recovered "
            "gate for a different gate. Produce one minimal focused delta that must retain "
            "every recovered checkpoint under fresh paired replay. "
        )
    if (
        '"evidence_incomplete": true' in feedback_text
        or '"a1_groundedness": 2' in feedback_text
        or "semantically_insufficient_evidence" in feedback_text
    ):
        instructions += (
            "This candidate has already completed authoritative replay and produced "
            "judge-scored task output. Preserve every candidate-owned replay file "
            "byte-for-byte and repair only the reusable target skill content; do not "
            "change capability declarations, compilers, runtimes, probes, or fixtures. "
            "A successful handshake, HTTP status, structured envelope, metadata record, "
            "or tool-execution summary is not by itself task completion. Before finalizing, "
            "check whether a bounded non-compacted artifact excerpt or structured field "
            "directly supports each claim requested by the user. If not, make exactly one "
            "materially different bounded artifact-backed attempt, then immediately omit "
            "the unsupported claim or return an explicit insufficiency without another "
            "tool call. A source artifact must be captured directly from a tool or primary "
            "source. Never write your own draft answer, synthesis, inferred comparison, or "
            "remembered knowledge into a file and then cite that self-authored file as "
            "evidence. Read the captured source artifact before answering, and make every "
            "ledger entry refer to an existing canonical artifact path plus the exact "
            "bounded excerpt or structured fields actually used. Do not claim an artifact "
            "or manifest-entry count unless it was computed from the final manifest. "
            "Apply this support-or-omit rule to factual, configuration, "
            "bibliographic, quantitative, symbolic, and quoted claims; never fill a missing "
            "claim from memory or encode case-specific endpoints or prompts. "
        )
    if (
        "duplicate_prior_candidate" in feedback_text
        or "repair_parent_target_delta_lost" in feedback_text
        or "repair_parent_semantic_delta_missing" in feedback_text
    ):
        instructions += (
            "The previous repair repeated or discarded its focused parent delta. "
            "Start from repair_focus.repair_candidate_package.content, preserve its "
            "verified target behavior, and make a materially different semantic "
            "change that implements the active typed repair action. Returning "
            "current_content, whitespace-only edits, rationale-only claims, or the "
            "unchanged focused package is invalid. "
        )
    if (
        "evaluation_support_bootstrap_only" in feedback_text
        or (
            '"candidate_status": "prerequisite"' in feedback_text
            and "target_behavior_delta" in feedback_text
        )
    ):
        instructions += (
            "The focused package is a verified evaluation-support prerequisite, not a "
            "failed replay implementation. Preserve every candidate-owned support file "
            "byte-for-byte and produce a semantic change to the releasable target content. "
            "A files-only response, current_content, frontmatter-only provenance change, "
            "or another support repair does not satisfy this composition frontier. "
        )
    if "repair_candidate_task_behavior" in required_runtime_transitions:
        instructions += (
            "The active counterexample occurred during task rollout, so SKILL.md is "
            "an authorized and required producer branch. Start from the focused "
            "package content and materially change the guidance that caused the "
            "observed action sequence; replay compiler/runtime edits alone are "
            "insufficient. Preserve inherited typed support constraints while "
            "removing redundant collection, stopping immediately after valid "
            "artifact-backed evidence, and never embedding a replay endpoint literal "
            "in generated evidence when a stable source identifier is sufficient. "
        )
    if (
        "align_compiler_runtime_recorded_response_selection"
        in failure_codes
    ):
        instructions += (
            "This failure proves compiler/runtime recorded-response selector drift. "
            "Change both the manifest entrypoint compiler and at least one runtime "
            "implementation path named by required_branch_paths. Use one canonical "
            "gateway discovery, payload traversal, recursive JSON decoding, ordering, "
            "and fallback algorithm on both sides. The compiler's response_contains "
            "must be a scalar descendant of the exact recorded container projected by "
            "the runtime from AWORLD_REPLAY_RESPONSE_INDEX; do not weaken the runtime "
            "to echo the mismatched diagnostic preview. "
        )
    if (
        "invalid_replay_capability_compile" in failure_codes
        or "repair_capability_compile_failed" in failure_codes
        or "invalid_replay_capability_compile" in feedback_text
        or "repair_capability_compile_failed" in feedback_text
    ):
        instructions += (
            "Repair the exact schema layer named by required_manifest_contract, "
            "required_compile_result_contract, and layering_rules. The manifest "
            "protocol remains aworld.replay.subprocess.v1 and handles contains only "
            "request requirement kinds. skill_runtime belongs only in a compiled "
            "result service's transport; runtime_required is only request status. "
            "Do not guess alternative protocol names or add unsupported manifest "
            "fields. Preserve the compiler's --request/--output interface and write "
            "the declared result schema to output/result.json. The framework creates "
            "only the output root; create every declared subdirectory and its parents "
            "before copying or writing fixtures or runtime artifacts. Evidence inputs "
            "may be immutable: copy bytes with shutil.copyfile or explicit read/write, "
            "never shutil.copy2, copymode, or copied source metadata. Use a "
            "requirement-qualified destination or deduplicate before writing so repeated "
            "source selection cannot overwrite a read-only output fixture. "
            "The compiler runs as a deterministic, network-disabled artifact transform: "
            "do not bind or connect sockets, allocate a live port, launch runtime code, or "
            "probe readiness. Declare runtime_entrypoint in result.json and let the framework "
            "start it later with an allocated port. request requirements contain evidence_refs "
            "as string keys; resolve them through request.evidence_derivations before reading "
            "source-object fields. Do not call .get on an evidence_ref string. "
        )
    if fixture_probe_constraints:
        instructions += (
            "The fixture_probe_constraints list is a shape-complete compiler "
            "contract, not a representative example. The framework owns the canonical "
            "fixture record selector and binds each compiled data-plane probe to a "
            "stable response_record_id. Keep a non-empty response_contains declaration "
            "for the protocol shape, but never truncate or synthesize a fixture value: "
            "the frozen probe assertion is replaced by the exact bounded scalar selected "
            "from the immutable response-index record. Runtime code must consume the "
            "framework-provided response index and stable requirement/service/record "
            "bindings so the response comes from that same record. Reject bool before "
            "testing int or float. Do not use mapping keys and do not concatenate "
            "multiple scalar descendants into one assertion. Do not serialize a "
            "metadata wrapper containing fixture hashes, byte counts, shapes, keys, "
            "or previews; those values are not descendants of the fixture payload. "
            "When an evidence_ref resolves to multiple source objects, first restrict "
            "selection to objects with a non-empty response_index_path and a positive "
            "integer response_record_count, then prefer the greatest record count (and "
            "byte length only as a deterministic tie-break). Never rank all sources by "
            "smallest byte_length before recorded-response eligibility: a compact tool-call "
            "fragment can contain no replayable response, leaving only an invented fallback. "
            "Treat required_branch_paths as the producer repair boundary: "
            "a schema constraint from another layer is a preservation invariant and "
            "cannot substitute for materially changing the named failing branch. "
        )
    if schema_field_constraints:
        instructions += (
            "The schema_field_constraints list is an executable, shape-complete contract "
            "with typed value domains. A constraint whose value_domain is source_behavior "
            "describes behavior detected by static analysis in the required source "
            "branch. Its field_path is an analyzer-owned predicate name, not a JSON "
            "or environment path: implement the expected behavior in source code and "
            "never assign, overwrite, or synthesize that path at runtime. For the "
            "source_behavior domain, required_operations is a conjunctive structural "
            "data-flow contract: implement every listed operation in the same bounded "
            "execution path. An operation that binds one value to another requires "
            "syntactically provable value flow (direct use or an explicit parameter), "
            "not disconnected helpers or matching names. An operation that projects "
            "a field directly requires explicit access to that field rather than a "
            "generic recursive fallback. forbidden_operations names structural substitutions that "
            "must be absent. These operation tokens describe behavior, not identifiers "
            "to copy into comments, strings, or metadata; source must actually realize "
            "the data flow and the analyzer will verify it. For the "
            "default schema_value domain, treat field_path as an absolute path from "
            "the root of schema_layer: a path with no dot or "
            "[*] names exactly one top-level field, while [*] selects every array "
            "member. A selector [*@predicate.path:value] applies only to members "
            "correlated with an input or related-schema record whose predicate "
            "matches value; preserve that condition for mixed and multi-member "
            "inputs rather than forcing the value on unrelated members. Do not "
            "satisfy a root-field constraint by adding a similarly "
            "named field to a nested service or probe. Apply every rule to every "
            "instance selected by its field_path, including services or probes "
            "produced for different trajectory members. Consult the retained "
            "capability_contracts schema shape for field placement. enum rules permit "
            "only their expected values; type rules permit only their expected "
            "JSON types; contains_all requires every expected array member; required, "
            "non_empty, unique, starts_with, max_chars, and max_items rules retain "
            "their literal "
            "schema meanings. Keep schema_layer "
            "boundaries intact: a valid manifest value is not automatically valid "
            "in a compile-result service field. Repair the compiler or manifest "
            "that owns the field rather than suppressing validation, changing the "
            "diagnostic, or special-casing a recorded case. "
        )
    if requires_http_1_1_websocket_upgrade:
        instructions += (
            "The runtime advertises a WebSocket endpoint, so its actual upgrade "
            "status line must use HTTP/1.1. Change the live runtime producer, not "
            "the compiler declaration or diagnostic. If the runtime subclasses "
            "Python BaseHTTPRequestHandler, set protocol_version = \"HTTP/1.1\" "
            "on the handler class; for another server implementation, configure "
            "the equivalent wire protocol. Keep status 101 and the Upgrade, "
            "Connection, and Sec-WebSocket-Accept headers, then let the executable "
            "WebSocket probe prove the repair. "
        )
    if requires_unique_result_fixtures:
        instructions += (
            "The compile-result fixtures collection must contain each normalized "
            "relative path exactly once. Multiple requirements may select the same "
            "evidence_ref or identical bytes, so changing only the hash algorithm, "
            "file suffix, or evidence-ref sanitizer is not a repair. Implement one "
            "of two general topologies: allocate a requirement-qualified unique path "
            "for every requirement, or reuse a single fixture path while merging all "
            "fixture_evidence_refs/provenance bindings that point to it. In either "
            "topology, create and append the fixture declaration once per path and "
            "bind every service and requirement to that canonical declaration. "
        )
    if (
        "response_contains" in feedback_text
        and "at most 4096 characters" in feedback_text
    ):
        instructions += (
            "The focused compiler emitted an overlong protocol assertion. Change "
            "the compiler path that derives response_contains so every emitted "
            "value is a non-empty fixture-derived scalar substring of at most 4096 "
            "characters. Bound the assertion after selecting the recorded scalar; "
            "the runtime must still return the complete recorded response container. "
            "Do not hard-code fixture text or weaken the fixture-derivation check. "
        )
    if runtime_response_constraints or (
        "surrounding recorded response context" in feedback_text
    ):
        maximum_response_bytes = min(
            (
                int(item.get("maximum_response_bytes"))
                for item in runtime_response_constraints
                if isinstance(item.get("maximum_response_bytes"), int)
                and not isinstance(item.get("maximum_response_bytes"), bool)
                and int(item.get("maximum_response_bytes")) > 0
            ),
            default=48 * 1024,
        )
        minimum_scalar_descendants = max(
            (
                int(item.get("projection_minimum_scalar_descendants"))
                for item in runtime_response_constraints
                if isinstance(
                    item.get("projection_minimum_scalar_descendants"), int
                )
                and not isinstance(
                    item.get("projection_minimum_scalar_descendants"), bool
                )
                and int(
                    item.get("projection_minimum_scalar_descendants")
                ) > 0
            ),
            default=2,
        )
        minimum_scalar_descendant_label = (
            "two"
            if minimum_scalar_descendants == 2
            else str(minimum_scalar_descendants)
        )
        response_bound_label = (
            "below 48 KiB"
            if maximum_response_bytes == 48 * 1024
            else f"within {maximum_response_bytes} bytes"
        )
        instructions += (
            "A typed runtime_response_constraint proves that the failed data-plane "
            "body did not expose enough bounded context from one "
            "record. AWORLD_REPLAY_RESPONSE_INDEX is already generated by the framework; "
            "do not create, declare, embed, or copy another response-index sidecar in the "
            "compiler. In the runtime, open that environment value as a file path and "
            "select the first record whose non_empty and protocol_eligible fields are true "
            "for the incoming operation; transport_ready records are ordered first "
            "(or use the deterministic first operation for an operation-less HTTP probe). "
            "Recursively JSON-decode its value. If its serialized container fits "
            f"{response_bound_label}, return that complete container "
            "without truncating, wrapping, or selecting a preview. If it is larger, "
            "construct a deterministic "
            "bounded mapping/list projection from the same record: retain container shape "
            f"and at least {minimum_scalar_descendant_label} non-empty scalar descendants "
            "when available, truncate or "
            "omit oversized text fields, and verify the final serialized response remains "
            f"{response_bound_label}. This bounded projection is the "
            "required surrounding context; "
            "returning one scalar, a preview-only wrapper, sidecar metadata, or a body "
            "larger than the 64 KiB protocol reader is non-conforming. "
        )
    if runtime_artifact_constraints:
        artifact_contracts = ", ".join(
            (
                f"{item.get('relative_path')} owned by "
                f"{item.get('producer_layer')} and available at "
                f"{item.get('availability_milestone')} via "
                f"{item.get('write_mode')} writes"
            )
            for item in runtime_artifact_constraints[:8]
        )
        instructions += (
            "The typed runtime_artifact_constraints define an execution-time "
            "producer contract, not a documentation or compiler schema repair. "
            f"Required artifacts: {artifact_contracts}. Change only the authorized "
            "producer source paths. Create and update each artifact before its "
            "availability milestone; a write deferred to shutdown/finally does not "
            "satisfy a post-probe-pre-shutdown contract. Keep writes bounded, "
            "non-empty when required, and preserve every required record field and "
            "direction. Do not move the artifact into the compiler, weaken the "
            "validator, or merely claim that a late buffered write exists. "
        )
    if "candidate_conformance_strategy_switch_required" in all_feedback_text:
        instructions += (
            "The same typed conformance fingerprint survived the prior focused repair. "
            "The attached counterexample_contracts are the authority: a topology change "
            "does not count unless the original counterexample checks pass. Change the "
            "failing response construction or data-flow topology itself; do "
            "not submit another rename, refactor-only edit, rationale-only claim, or an "
            "equivalent projection with different helper boundaries. Preserve the typed "
            "contract while selecting one materially different bounded algorithm. "
        )
    if requires_fixture_reconstruction:
        instructions += (
            "Obey required_reconstruction_algorithm and forbidden_derivations "
            "literally. For recorded-response gateway repair, phase 1 must recurse "
            "through mapping values and sequence items at arbitrary bounded nesting "
            "and collect only action_result or tool_outputs subtrees before phase 2 "
            "starts. A helper that returns every non-mapping input unchanged without "
            "first traversing sequences is non-conforming. Do not select scalars or "
            "metadata until the complete gateway list is known. Return the surrounding "
            "decoded recorded container, not only the assertion scalar. The payload-key "
            "set content/response/result/output/body/data must be consumed by phase 2; "
            "merely declaring it while traversing every gateway dict value is "
            "non-conforming. For each gateway call the payload collector, then the "
            "scalar selector on resulting payload subtrees. Calling the scalar selector "
            "directly on a gateway is forbidden. Phase 2 is the processing of payloads "
            "inside found gateways; only an empty complete gateway list permits a root "
            "fallback. Decode JSON-encoded payload strings recursively and reject bool "
            "before int or float. Correlate each operation and bounded parameters with "
            "its deterministic recorded-response cursor. The repair_conformance "
            "contract's required_fixture_probe_operations cannot be replaced by a "
            "later repetition. The framework-finalized response_contains remains an "
            "exact recorded scalar leaf and must never be recreated by truncation, "
            "while the runtime response must carry the surrounding decoded container or "
            "a deterministic under-48-KiB projection retaining at least two scalar "
            "descendants from that same container. "
            "Never remove or relocate the contract's exact_probe. "
            "AWORLD_REPLAY_RESPONSE_INDEX is a framework-supplied filesystem path to "
            "a JSON object with a records array, not an integer or compiler-owned "
            "output. Open that path, select a record whose non_empty field is true for "
            "the incoming operation, and project record['value']; do not substitute a "
            "recursive scan of the raw fixture. Use record['payload_path'] only when "
            "value is absent. Index fields are metadata, not task output. "
        )
    if "finalize_after_successful_endpoint_interaction" in feedback_text:
        instructions += (
            "The replay runtime already completed the task-plane interaction. "
            "Preserve its candidate-owned files byte-for-byte and repair the target "
            "skill content with a small reusable finalization delta. Return content "
            "or patch_intent that requires immediate artifact and manifest persistence "
            "after the first successful structured extraction, stops redundant "
            "collection once sufficient evidence exists, and returns a bounded evidence "
            "ledger. Do not change readiness, protocol, compiler, or runtime behavior "
            "for this failure. "
        )
    instructions += (
        "Treat SKILL.md and every candidate-owned file as one atomic release package. "
        "Include every added or changed package file in files. Every concrete replay/... "
        "path named by content or patch_intent must already exist in "
        "target_package_inventory or be supplied as an upsert in files; never describe "
        "a file that is absent from the candidate package. A files[*].content value "
        "starts with the file's first byte: never place a Markdown filename heading "
        "such as '# replay/capability.json' inside JSON or source file content. "
        "Every Markdown fenced block in content or patch_intent must be balanced inside the "
        "same field. Do not duplicate or partially splice an existing section. Return the "
        "value of expected_output as exactly one JSON object without a "
        "wrapper. Use at most one of content or patch_intent; both may be omitted "
        "when candidate-owned files implement the reusable behavior delta.\n"
    )
    return instructions


def _validate_mutator_output_context(
    output: Mapping[str, Any],
    *,
    request: OptimizerRequest,
    candidate_index: int,
) -> Mapping[str, Any]:
    """Validate request-bound semantics while same-slot repair is available."""

    try:
        output = _canonicalize_single_trailing_patch_fence_output(
            output,
            request=request,
        )
        output = _canonicalize_replay_manifest_path_heading_output(
            output,
            request=request,
            candidate_index=candidate_index,
        )
        output = _canonicalize_recorded_response_index_output(
            output,
            request=request,
            candidate_index=candidate_index,
        )
        output = _canonicalize_fixture_source_selector_output(
            output,
            request=request,
            candidate_index=candidate_index,
        )
        content, _, _, files = _materialize_mutator_output(
            output,
            request=request,
            candidate_index=candidate_index,
        )
        if request.target.target_type == "skill":
            base_structure = validate_skill_markdown_structure(
                _focused_repair_patch_base(
                    request,
                    candidate_index=candidate_index,
                )
            )
            structure = validate_skill_markdown_structure(content)
            if base_structure.passed and not structure.passed:
                raise CandidateSemanticValidationError(
                    structure.code,
                    structure.reason,
                    field_path=structure.field_path,
                    representation=(
                        _candidate_output_representation(output).value
                    ),
                    repairable=True,
                    allowed_improvement_signal_ids=(
                        exposed_improvement_signal_ids(request)
                    ),
                    details={
                        "contract_fingerprint": (
                            structure.contract_fingerprint
                        ),
                        **dict(structure.details),
                    },
                )
        declared_addressed_improvement_signal_ids(request, output)
        files, _ = _overlay_repair_focus_files(
            request,
            candidate_index=candidate_index,
            candidate_files=files,
        )
        files, _ = _preserve_existing_replay_package_for_target_delta(
            request,
            candidate_index=candidate_index,
            candidate_files=files,
        )
        candidate = CandidateVariant(
            candidate_id="pending",
            target=request.target,
            content=content,
            rationale="candidate package validation",
            target_fingerprint=request.target_fingerprint,
            files=files,
        )
        reference_report = candidate_package_reference_report(
            candidate,
            existing_paths=request.target_package_inventory,
        )
        if not reference_report["closed"]:
            missing = ", ".join(
                reference_report["missing_referenced_paths"]
            )
            raise CandidateMaterializationError(
                CandidateMaterializationCode.PACKAGE_REFERENCE_MISSING,
                "candidate package is missing files referenced by skill content: "
                + missing,
                field_path=CandidateFailureField.FILES,
            )
        context = request.evolution_context or compile_evolution_context(request)
        repair_focus = context.repair_focus_for_candidate(
            candidate_index=candidate_index
        )
        _validate_judge_repair_target_delta(
            request,
            repair_focus=repair_focus,
            candidate_content=content,
        )
        _validate_prerequisite_composition_target_delta(
            request,
            repair_focus=repair_focus,
            candidate_content=content,
        )
        _validate_focused_repair_mutation_scope(
            request,
            repair_focus=repair_focus,
            candidate_content=content,
            candidate_files=files,
        )
        private_contract = (
            None
            if (
                isinstance(repair_focus, Mapping)
                and (
                    _repair_feedback_reached_judged_task_output(repair_focus)
                    or _repair_feedback_is_prerequisite_composition(
                        repair_focus
                    )
                )
            )
            else compile_repair_conformance_contract(repair_focus)
        )
        if private_contract is not None:
            conformance = evaluate_candidate_source_conformance(
                candidate,
                private_contract,
            )
            if (
                not conformance.passed
                and conformance.repairable
                and conformance.failure_class == "candidate"
            ):
                raise CandidateSemanticValidationError(
                    conformance.code,
                    conformance.reason,
                    field_path=CandidateFailureField.FILES.value,
                    representation=CandidateRepresentation.CANDIDATE_PACKAGE.value,
                    repairable=True,
                    allowed_improvement_signal_ids=(
                        exposed_improvement_signal_ids(request)
                    ),
                    details={
                        "repair_conformance": conformance.to_dict(),
                    },
                )
    except CandidateSemanticValidationError:
        raise
    except CandidateMaterializationError as exc:
        raise _candidate_semantic_error(
            exc,
            output=output,
            request=request,
        ) from exc
    except ValueError as exc:
        raise _candidate_semantic_error(
            exc,
            output=output,
            request=request,
        ) from exc
    return output


_MARKDOWN_FENCE_LINE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _canonicalize_replay_manifest_path_heading_output(
    output: Mapping[str, Any],
    *,
    request: OptimizerRequest,
    candidate_index: int,
) -> Mapping[str, Any]:
    """Remove one model-added filename label from an otherwise valid manifest.

    Structured-output models sometimes include ``# replay/capability.json`` as
    the first line of that file's content.  The label is presentation metadata,
    not JSON, and otherwise makes every focused repair inherit a manifest that
    fails before the compiler can expose a typed repair constraint.  Normalize
    only this exact path-local shape and only when the remaining bytes decode to
    a JSON object.  Arbitrary comments and malformed manifests remain invalid.
    """

    if request.target.target_type != "skill":
        return output
    try:
        _, _, _, explicit_files = _materialize_mutator_output(
            output,
            request=request,
            candidate_index=candidate_index,
        )
    except (CandidateMaterializationError, ValueError):
        return output

    manifest_indexes = [
        index
        for index, item in enumerate(explicit_files)
        if item.path == "replay/capability.json"
        and item.operation == "upsert"
        and isinstance(item.content, str)
    ]
    if len(manifest_indexes) != 1:
        return output
    manifest_index = manifest_indexes[0]
    manifest = explicit_files[manifest_index]
    assert isinstance(manifest.content, str)
    first_line, separator, remainder = manifest.content.partition("\n")
    if not separator or first_line.rstrip("\r") != "# replay/capability.json":
        return output
    try:
        decoded = json.loads(remainder)
    except (json.JSONDecodeError, TypeError, ValueError):
        return output
    if not isinstance(decoded, Mapping):
        return output

    normalized_files = list(explicit_files)
    normalized_files[manifest_index] = CandidateFileDelta(
        path=manifest.path,
        operation=manifest.operation,
        content=remainder,
        executable=manifest.executable,
    )
    return _replace_output_files(output, tuple(normalized_files))


def _canonicalize_single_trailing_patch_fence_output(
    output: Mapping[str, Any],
    *,
    request: OptimizerRequest,
) -> Mapping[str, Any]:
    """Close one patch-local trailing fence without repairing arbitrary Markdown.

    Structured-output models occasionally omit only the final fence delimiter from a
    bounded ``patch_intent`` body.  The complete base skill is independently valid, and
    the omitted delimiter otherwise turns a representation defect into an authoritative
    candidate.  Normalize only one such patch-local defect.  Full-content responses,
    multiple affected operations, ambiguous delimiter shapes, and all other Markdown
    damage remain unchanged and fail through the normal structural validator.
    """

    if request.target.target_type != "skill":
        return output
    patch_intent = output.get("patch_intent")
    if not isinstance(patch_intent, Mapping):
        return output
    operations = patch_intent.get("operations")
    if not isinstance(operations, list):
        return output

    repairs: list[tuple[int, str]] = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, Mapping):
            continue
        body = operation.get("content")
        if not isinstance(body, str):
            continue
        delimiter = _single_trailing_unclosed_fence_delimiter(body)
        if delimiter is not None:
            repairs.append((index, delimiter))
    if len(repairs) != 1:
        return output

    repair_index, delimiter = repairs[0]
    normalized_operations: list[object] = list(operations)
    repaired_operation = dict(normalized_operations[repair_index])
    body = repaired_operation.get("content")
    assert isinstance(body, str)
    repaired_operation["content"] = body.rstrip("\n") + "\n" + delimiter + "\n"
    normalized_operations[repair_index] = repaired_operation
    normalized_patch = dict(patch_intent)
    normalized_patch["operations"] = normalized_operations
    normalized_output = dict(output)
    normalized_output["patch_intent"] = normalized_patch
    return normalized_output


def _single_trailing_unclosed_fence_delimiter(content: str) -> str | None:
    open_character: str | None = None
    open_length = 0
    for line in content.splitlines():
        match = _MARKDOWN_FENCE_LINE.fullmatch(line)
        if match is None:
            continue
        marker = match.group(1)
        suffix = match.group(2)
        if open_character is None:
            open_character = marker[0]
            open_length = len(marker)
            continue
        if (
            marker[0] == open_character
            and len(marker) >= open_length
            and not suffix.strip()
        ):
            open_character = None
            open_length = 0
    if open_character is None:
        return None
    return open_character * open_length


def _canonicalize_recorded_response_index_output(
    output: Mapping[str, Any],
    *,
    request: OptimizerRequest,
    candidate_index: int,
) -> Mapping[str, Any]:
    """Normalize one provably equivalent generated reader-helper chain.

    Model repairs commonly split the response-index environment read and JSON
    reader into two returning helpers.  That implementation is operationally
    reasonable, but return-value propagation is deliberately outside the
    bounded source analyzer.  Rewrite only the conservative two-assignment
    shape whose helpers demonstrably own those operations, then require the
    unchanged analyzer to prove the rewritten source before accepting it.

    This is a candidate-source normalization, not a gate bypass: unrelated
    source shapes, missing ``records``/``value`` projections, and attribute or
    container propagation remain rejected and continue through same-slot LLM
    repair.
    """

    context = request.evolution_context or compile_evolution_context(request)
    repair_focus = context.repair_focus_for_candidate(
        candidate_index=candidate_index
    )
    contract = compile_repair_conformance_contract(repair_focus)
    required_operations = {
        "read_environment_binding_as_path",
        "bind_environment_path_to_json_file_reader",
        "access_records_array",
        "project_record_value_field_directly",
    }
    if contract is None or not any(
        constraint.schema_layer == "runtime"
        and constraint.value_domain == "source_behavior"
        and constraint.field_path
        == "environment.AWORLD_REPLAY_RESPONSE_INDEX.consumer"
        and REPLAY_RESPONSE_INDEX_CONSUMER in constraint.expected
        and required_operations.issubset(constraint.required_operations)
        for constraint in contract.schema_field_constraints
    ):
        return output

    try:
        _, _, _, explicit_files = _materialize_mutator_output(
            output,
            request=request,
            candidate_index=candidate_index,
        )
    except (CandidateMaterializationError, ValueError):
        return output

    runtime_paths = frozenset(contract.runtime_paths)
    rewritten_files: list[CandidateFileDelta] = []
    changed = False
    for item in explicit_files:
        rewritten = None
        if (
            item.path in runtime_paths
            and item.path in contract.required_branch_paths
            and item.operation == "upsert"
            and isinstance(item.content, str)
        ):
            rewritten = _canonicalize_recorded_response_index_reader_chain(
                item.content
            )
        if rewritten is not None:
            item = CandidateFileDelta(
                path=item.path,
                operation=item.operation,
                content=rewritten,
                executable=item.executable,
            )
            changed = True
        rewritten_files.append(item)
    if not changed:
        return output
    return _replace_output_files(output, tuple(rewritten_files))


def _replace_output_files(
    output: Mapping[str, Any],
    files: tuple[CandidateFileDelta, ...],
) -> Mapping[str, Any]:
    serialized = [
        {
            "path": item.path,
            "operation": item.operation,
            **(
                {"content": item.content}
                if isinstance(item.content, str)
                else {}
            ),
            **({"executable": True} if item.executable else {}),
        }
        for item in files
    ]
    normalized = dict(output)
    envelope = normalized.get("expected_output")
    if isinstance(envelope, Mapping):
        normalized["expected_output"] = {**dict(envelope), "files": serialized}
    else:
        normalized["files"] = serialized
    return normalized


def _canonicalize_fixture_source_selector_output(
    output: Mapping[str, Any],
    *,
    request: OptimizerRequest,
    candidate_index: int,
) -> Mapping[str, Any]:
    """Prefer evidence with recorded responses for fixture-derived probes."""

    context = request.evolution_context or compile_evolution_context(request)
    repair_focus = context.repair_focus_for_candidate(
        candidate_index=candidate_index
    )
    contract = compile_repair_conformance_contract(repair_focus)
    if (
        contract is None
        or "protocol_probe_not_fixture_derived" not in contract.failure_codes
        or not contract.requires_compiler_fixture_reconstruction
        or contract.compiler_path is None
        or contract.compiler_path not in contract.required_branch_paths
    ):
        return output
    try:
        _, _, _, explicit_files = _materialize_mutator_output(
            output,
            request=request,
            candidate_index=candidate_index,
        )
    except (CandidateMaterializationError, ValueError):
        return output

    rewritten_files: list[CandidateFileDelta] = []
    changed = False
    for item in explicit_files:
        rewritten = None
        if (
            item.path == contract.compiler_path
            and item.operation == "upsert"
            and isinstance(item.content, str)
        ):
            rewritten = _canonicalize_fixture_source_selector(item.content)
        if rewritten is not None:
            item = CandidateFileDelta(
                path=item.path,
                operation=item.operation,
                content=rewritten,
                executable=item.executable,
            )
            changed = True
        rewritten_files.append(item)
    if not changed:
        return output
    return _replace_output_files(output, tuple(rewritten_files))


_MINIMUM_BYTE_SOURCE_SELECTOR = '''def _select_source(evidence_ref: str, derivations: dict) -> dict | None:
    sources = derivations.get(evidence_ref, [])
    if not sources:
        return None
    ranked = sorted(sources, key=lambda s: s.get("byte_length", 0))
    return ranked[0]
'''

_RECORDED_RESPONSE_SOURCE_SELECTOR = '''def _select_source(evidence_ref: str, derivations: dict) -> dict | None:
    sources = derivations.get(evidence_ref, [])
    if not sources:
        return None
    eligible = [
        source
        for source in sources
        if isinstance(source, dict)
        and isinstance(source.get("response_index_path"), str)
        and bool(source["response_index_path"].strip())
        and isinstance(source.get("response_record_count"), int)
        and not isinstance(source.get("response_record_count"), bool)
        and source["response_record_count"] > 0
    ]
    if eligible:
        return max(
            eligible,
            key=lambda source: (
                source["response_record_count"],
                source.get("byte_length", 0)
                if isinstance(source.get("byte_length"), int)
                and not isinstance(source.get("byte_length"), bool)
                else 0,
            ),
        )
    ranked = sorted(sources, key=lambda source: source.get("byte_length", 0))
    return ranked[0]
'''


def _canonicalize_fixture_source_selector(source: str) -> str | None:
    """Rewrite the exact selector that discards recorded-response evidence."""

    try:
        tree = ast.parse(source)
        legacy_function = ast.parse(_MINIMUM_BYTE_SOURCE_SELECTOR).body[0]
    except SyntaxError:
        return None
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and ast.dump(node, include_attributes=False)
        == ast.dump(legacy_function, include_attributes=False)
    ]
    if len(matches) != 1:
        return None
    function = matches[0]
    lines = source.splitlines(keepends=True)
    start = int(function.lineno) - 1
    end = int(function.end_lineno or function.lineno)
    rewritten = (
        "".join(lines[:start])
        + _RECORDED_RESPONSE_SOURCE_SELECTOR
        + "".join(lines[end:])
    )
    try:
        compile(rewritten, "<candidate-compiler>", "exec")
    except (SyntaxError, TypeError, ValueError):
        return None
    return rewritten


def _canonicalize_recorded_response_index_reader_chain(
    source: str,
) -> str | None:
    proof = recorded_response_index_source_behavior_proof(source)
    missing_operations = set(proof.get("missing_operations", ()))
    operation_status = proof.get("operation_status")
    if (
        proof.get("proven") is True
        or not isinstance(operation_status, Mapping)
        or operation_status.get("access_records_array") is not True
        or operation_status.get("project_record_value_field_directly") is not True
        or "bind_environment_path_to_json_file_reader" not in missing_operations
        or not missing_operations.issubset(
            {
                "read_environment_binding_as_path",
                "bind_environment_path_to_json_file_reader",
            }
        )
    ):
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    if not _has_direct_module_import(tree, "os") or not _has_direct_module_import(
        tree, "json"
    ):
        return None

    function_nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    function_name_counts: dict[str, int] = {}
    for function in function_nodes:
        function_name_counts[function.name] = (
            function_name_counts.get(function.name, 0) + 1
        )
    functions = {
        node.name: node
        for node in function_nodes
        if function_name_counts[node.name] == 1 and not node.decorator_list
    }
    environment_helpers = {
        name
        for name, function in functions.items()
        if isinstance(function, ast.FunctionDef)
        and _function_is_transparent_environment_reader(function)
    }
    json_reader_helpers = {
        name
        for name, function in functions.items()
        if isinstance(function, ast.FunctionDef)
        and _function_reads_json_parameter(function)
    }
    if not environment_helpers or not json_reader_helpers:
        return None

    lines = source.splitlines(keepends=True)
    proven_rewrites: list[str] = []
    for function in functions.values():
        locally_bound = {
            node.id
            for node in ast.walk(function)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        }
        if "os" in locally_bound or "json" in locally_bound:
            continue
        body = function.body
        for index in range(len(body) - 1):
            path_assignment = body[index]
            if not isinstance(path_assignment, ast.Assign):
                continue
            path_name = _simple_assignment_name(path_assignment)
            if path_name is None:
                continue
            path_call = getattr(path_assignment, "value", None)
            if not (
                isinstance(path_call, ast.Call)
                and isinstance(path_call.func, ast.Name)
                and path_call.func.id in environment_helpers
                and _response_index_key_reaches_call(
                    tree,
                    caller=function,
                    call=path_call,
                )
            ):
                continue
            reader_positions = [
                position
                for position in range(index + 1, len(body))
                if _reader_assignment_consumes_path(
                    body[position],
                    path_name=path_name,
                    json_reader_helpers=json_reader_helpers,
                )
            ]
            if len(reader_positions) != 1:
                continue
            reader_position = reader_positions[0]
            if not all(
                _is_response_path_absence_guard(
                    statement,
                    path_name=path_name,
                )
                for statement in body[index + 1 : reader_position]
            ):
                continue
            index_assignment = body[reader_position]
            index_name = _simple_assignment_name(index_assignment)
            reader_call = getattr(index_assignment, "value", None)
            if not (
                index_name is not None
                and isinstance(reader_call, ast.Call)
                and isinstance(reader_call.func, ast.Name)
                and reader_call.func.id in json_reader_helpers
                and reader_call.args
                and isinstance(reader_call.args[0], ast.Name)
                and reader_call.args[0].id == path_name
                and any(
                    isinstance(node, ast.Name)
                    and isinstance(node.ctx, ast.Load)
                    and node.id == index_name
                    for statement in body[reader_position + 1 :]
                    for node in ast.walk(statement)
                )
            ):
                continue
            start = int(path_assignment.lineno) - 1
            end = int(path_assignment.end_lineno or path_assignment.lineno)
            indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
            replacement = (
                f'{indent}{path_name} = os.environ.get('
                '"AWORLD_REPLAY_RESPONSE_INDEX", "")\n'
            )
            rewritten = "".join(lines[:start]) + replacement + "".join(lines[end:])
            try:
                compile(rewritten, "<candidate-runtime>", "exec")
            except (SyntaxError, ValueError, TypeError):
                continue
            if recorded_response_index_source_behavior_proof(rewritten).get(
                "proven"
            ) is True:
                proven_rewrites.append(rewritten)
    unique_rewrites = list(dict.fromkeys(proven_rewrites))
    return unique_rewrites[0] if len(unique_rewrites) == 1 else None


def _reader_assignment_consumes_path(
    statement: ast.stmt,
    *,
    path_name: str,
    json_reader_helpers: set[str],
) -> bool:
    value = getattr(statement, "value", None)
    return bool(
        _simple_assignment_name(statement) is not None
        and isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in json_reader_helpers
        and len(value.args) == 1
        and isinstance(value.args[0], ast.Name)
        and value.args[0].id == path_name
        and not value.keywords
    )


def _is_response_path_absence_guard(
    statement: ast.stmt,
    *,
    path_name: str,
) -> bool:
    """Allow only ``if not path: return None`` between the two assignments."""

    return bool(
        isinstance(statement, ast.If)
        and isinstance(statement.test, ast.UnaryOp)
        and isinstance(statement.test.op, ast.Not)
        and isinstance(statement.test.operand, ast.Name)
        and statement.test.operand.id == path_name
        and not statement.orelse
        and len(statement.body) == 1
        and isinstance(statement.body[0], ast.Return)
        and (
            statement.body[0].value is None
            or (
                isinstance(statement.body[0].value, ast.Constant)
                and statement.body[0].value.value is None
            )
        )
    )


def _response_index_key_reaches_call(
    tree: ast.Module,
    *,
    caller: ast.FunctionDef | ast.AsyncFunctionDef,
    call: ast.Call,
) -> bool:
    """Prove the dynamic helper key is the framework response-index key."""

    if not call.args:
        return False
    key_argument = call.args[0]
    if (
        isinstance(key_argument, ast.Constant)
        and key_argument.value == "AWORLD_REPLAY_RESPONSE_INDEX"
    ):
        return True
    if not isinstance(key_argument, ast.Name):
        return False
    positional_parameters = [
        *caller.args.posonlyargs,
        *caller.args.args,
    ]
    parameter_index = next(
        (
            index
            for index, argument in enumerate(positional_parameters)
            if argument.arg == key_argument.id
        ),
        None,
    )
    if parameter_index is None:
        return False
    call_sites = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == caller.name
    ]
    call_function_nodes = {id(node.func) for node in call_sites}
    if any(
        isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id == caller.name
        and id(node) not in call_function_nodes
        for node in ast.walk(tree)
    ):
        return False
    if not call_sites:
        return False
    for call_site in call_sites:
        if any(isinstance(argument, ast.Starred) for argument in call_site.args):
            return False
        if any(keyword.arg is None for keyword in call_site.keywords):
            return False
        keyword_values = [
            keyword.value
            for keyword in call_site.keywords
            if keyword.arg == key_argument.id
        ]
        if len(call_site.args) > parameter_index:
            if keyword_values:
                return False
            resolved_argument = call_site.args[parameter_index]
        elif len(keyword_values) == 1:
            resolved_argument = keyword_values[0]
        else:
            return False
        if not (
            isinstance(resolved_argument, ast.Constant)
            and resolved_argument.value == "AWORLD_REPLAY_RESPONSE_INDEX"
        ):
            return False
    return True


def _has_direct_module_import(tree: ast.Module, module_name: str) -> bool:
    return any(
        isinstance(node, ast.Import)
        and any(
            alias.name == module_name and alias.asname in {None, module_name}
            for alias in node.names
        )
        for node in tree.body
    )


def _function_is_transparent_environment_reader(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    positional_parameters = [
        *function.args.posonlyargs,
        *function.args.args,
    ]
    if (
        len(positional_parameters) != 1
        or function.args.vararg is not None
        or function.args.kwarg is not None
        or function.args.kwonlyargs
    ):
        return False
    parameter_name = positional_parameters[0].arg
    body = list(function.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body.pop(0)
    if len(body) == 1 and isinstance(body[0], ast.Return):
        return _is_transparent_environment_get(
            body[0].value,
            parameter_name=parameter_name,
        )
    if not (
        len(body) == 2
        and isinstance(body[0], ast.Assign)
        and len(body[0].targets) == 1
        and isinstance(body[0].targets[0], ast.Name)
        and _is_transparent_environment_get(
            body[0].value,
            parameter_name=parameter_name,
        )
        and isinstance(body[1], ast.Return)
        and isinstance(body[1].value, ast.Name)
        and body[1].value.id == body[0].targets[0].id
    ):
        return False
    return True


def _is_transparent_environment_get(
    expression: ast.AST | None,
    *,
    parameter_name: str,
) -> bool:
    return bool(
        isinstance(expression, ast.Call)
        and isinstance(expression.func, ast.Attribute)
        and expression.func.attr == "get"
        and isinstance(expression.func.value, ast.Attribute)
        and isinstance(expression.func.value.value, ast.Name)
        and expression.func.value.value.id == "os"
        and expression.func.value.attr == "environ"
        and len(expression.args) == 2
        and isinstance(expression.args[0], ast.Name)
        and expression.args[0].id == parameter_name
        and isinstance(expression.args[1], ast.Constant)
        and expression.args[1].value == ""
        and not expression.keywords
    )


def _function_reads_json_parameter(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    parameter_names = {
        argument.arg
        for argument in (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        )
    }
    opened_parameter = False
    loads_opened_stream = False
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "open"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in parameter_names
        ):
            opened_parameter = True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "json"
            and node.func.attr in {"load", "loads"}
        ):
            loads_opened_stream = True
    return opened_parameter and loads_opened_stream


def _simple_assignment_name(statement: ast.stmt) -> str | None:
    if (
        isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
    ):
        return statement.targets[0].id
    if isinstance(statement, ast.AnnAssign) and isinstance(
        statement.target, ast.Name
    ):
        return statement.target.id
    return None


def _repair_focus_parent_candidate_ids(
    request: OptimizerRequest,
    *,
    candidate_index: int,
) -> tuple[str, ...]:
    context = request.evolution_context or compile_evolution_context(request)
    repair_focus = context.repair_focus_for_candidate(
        candidate_index=candidate_index
    )
    package = (
        repair_focus.get("repair_candidate_package")
        if isinstance(repair_focus, Mapping)
        else None
    )
    candidate_id = (
        package.get("candidate_id")
        if isinstance(package, Mapping)
        else None
    )
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        return ()
    return (candidate_id.strip(),)


def _validate_judge_repair_target_delta(
    request: OptimizerRequest,
    *,
    repair_focus: Mapping[str, object] | None,
    candidate_content: str,
) -> None:
    """Fail same-slot repair when a judged parent delta is lost or unchanged."""

    if not isinstance(repair_focus, Mapping) or not (
        _repair_feedback_reached_judged_task_output(repair_focus)
    ):
        return
    package = repair_focus.get("repair_candidate_package")
    parent_content = (
        package.get("content") if isinstance(package, Mapping) else None
    )
    if not isinstance(parent_content, str) or not parent_content.strip():
        return
    candidate_fingerprint = candidate_content_semantic_fingerprint(
        candidate_content
    )
    current_fingerprint = candidate_content_semantic_fingerprint(
        request.current_content
    )
    parent_fingerprint = candidate_content_semantic_fingerprint(parent_content)
    if candidate_fingerprint == current_fingerprint:
        code = "repair_parent_target_delta_lost"
        reason = (
            "judge-stage repair discarded the focused candidate target delta; "
            "edit the focused package content instead of returning current content"
        )
    elif candidate_fingerprint == parent_fingerprint:
        code = "repair_parent_semantic_delta_missing"
        reason = (
            "judge-stage repair did not materially change the focused candidate "
            "target content"
        )
    else:
        return
    raise CandidateSemanticValidationError(
        code,
        reason,
        field_path=CandidateFailureField.CONTENT.value,
        representation=CandidateRepresentation.CANDIDATE_PACKAGE.value,
        repairable=True,
        allowed_improvement_signal_ids=exposed_improvement_signal_ids(request),
        details={
            "parent_target_delta_required": True,
            "focused_candidate_id": (
                package.get("candidate_id")
                if isinstance(package, Mapping)
                else None
            ),
        },
    )


def _validate_prerequisite_composition_target_delta(
    request: OptimizerRequest,
    *,
    repair_focus: Mapping[str, object] | None,
    candidate_content: str,
) -> None:
    """Require real target behavior when inheriting verified support files."""

    if not isinstance(repair_focus, Mapping):
        return
    if not _repair_feedback_is_prerequisite_composition(repair_focus):
        return
    candidate_fingerprint = candidate_content_semantic_fingerprint(
        candidate_content
    )
    current_fingerprint = candidate_content_semantic_fingerprint(
        request.current_content
    )
    if candidate_fingerprint != current_fingerprint:
        return
    package = repair_focus.get("repair_candidate_package")
    raise CandidateSemanticValidationError(
        "prerequisite_target_behavior_delta_missing",
        (
            "evaluation-support composition must change releasable target "
            "behavior while inheriting the verified support package"
        ),
        field_path=CandidateFailureField.CONTENT.value,
        representation=CandidateRepresentation.CANDIDATE_PACKAGE.value,
        repairable=True,
        allowed_improvement_signal_ids=exposed_improvement_signal_ids(request),
        details={
            "composition_required": True,
            "focused_candidate_id": (
                package.get("candidate_id")
                if isinstance(package, Mapping)
                else None
            ),
        },
    )


def _validate_focused_repair_mutation_scope(
    request: OptimizerRequest,
    *,
    repair_focus: Mapping[str, object] | None,
    candidate_content: str,
    candidate_files: tuple[CandidateFileDelta, ...],
) -> None:
    """Enforce the typed producer boundary for support-package repairs.

    A compiler/runtime conformance failure authorizes changes to the source
    owners named by the contract.  It does not authorize a simultaneous rewrite
    of releasable skill guidance or unrelated support files.  Judge-stage and
    verified-prerequisite composition frontiers intentionally use different
    mutation rules and are handled by their dedicated validators.
    """

    if not isinstance(repair_focus, Mapping):
        return
    if _repair_feedback_reached_judged_task_output(
        repair_focus
    ) or _repair_feedback_is_prerequisite_composition(repair_focus):
        return
    failed_gates = repair_focus.get("failed_gates")
    active_source_frontier = isinstance(
        repair_focus.get("repair_conformance"),
        Mapping,
    ) or (
        isinstance(failed_gates, (list, tuple))
        and "candidate_repair_conformance" in failed_gates
    )
    if not active_source_frontier:
        # Cumulative contracts remain validation invariants for later repairs,
        # but historical source failures must not seize ownership from a newer
        # target-content or judge frontier.
        return
    contract = compile_repair_conformance_contract(repair_focus)
    if contract is None or not contract.required_branch_paths:
        return
    authorized_paths = frozenset(contract.required_branch_paths)
    package = repair_focus.get("repair_candidate_package")
    if not isinstance(package, Mapping):
        return

    parent_content = package.get("content")
    if (
        isinstance(parent_content, str)
        and parent_content.strip()
        and candidate_content_semantic_fingerprint(candidate_content)
        != candidate_content_semantic_fingerprint(parent_content)
        and "SKILL.md" not in authorized_paths
    ):
        raise CandidateSemanticValidationError(
            "focused_repair_target_scope_violation",
            (
                "support-package repair changed releasable target content; "
                "preserve the focused target delta and edit only typed source owners"
            ),
            field_path=CandidateFailureField.CONTENT.value,
            representation=CandidateRepresentation.CANDIDATE_PACKAGE.value,
            repairable=True,
            allowed_improvement_signal_ids=exposed_improvement_signal_ids(request),
            details={
                "focused_candidate_id": contract.focus_candidate_id,
                "authorized_paths": list(contract.required_branch_paths),
            },
        )

    raw_parent_files = package.get("files")
    if not isinstance(raw_parent_files, list):
        return
    parent_files = {
        item.path: item
        for item in validate_candidate_files(
            CandidateFileDelta(
                path=str(raw.get("path") or ""),
                operation=str(raw.get("operation") or "upsert"),
                content=(
                    raw.get("content")
                    if isinstance(raw.get("content"), str)
                    else None
                ),
                executable=bool(raw.get("executable", False)),
            )
            for raw in raw_parent_files
            if isinstance(raw, Mapping)
        )
    }
    unauthorized_changes: list[str] = []
    for item in validate_candidate_files(candidate_files):
        if item.path in authorized_paths:
            continue
        parent = parent_files.get(item.path)
        if parent != item:
            unauthorized_changes.append(item.path)
    for path in parent_files:
        if path not in authorized_paths and not any(
            item.path == path for item in candidate_files
        ):
            unauthorized_changes.append(path)
    if unauthorized_changes:
        raise CandidateSemanticValidationError(
            "focused_repair_file_scope_violation",
            (
                "support-package repair changed files outside the typed source-owner "
                "boundary"
            ),
            field_path=CandidateFailureField.FILES.value,
            representation=CandidateRepresentation.CANDIDATE_PACKAGE.value,
            repairable=True,
            allowed_improvement_signal_ids=exposed_improvement_signal_ids(request),
            details={
                "focused_candidate_id": contract.focus_candidate_id,
                "authorized_paths": sorted(authorized_paths),
                "unauthorized_changed_paths": sorted(set(unauthorized_changes)),
            },
        )


def _repair_feedback_is_prerequisite_composition(
    repair_focus: Mapping[str, object],
) -> bool:
    metrics = repair_focus.get("metrics")
    if not isinstance(metrics, Mapping):
        return False
    failed_gates = repair_focus.get("failed_gates", ())
    if isinstance(failed_gates, str):
        failed_gate_names = {failed_gates}
    elif isinstance(failed_gates, (list, tuple, set, frozenset)):
        failed_gate_names = {str(item) for item in failed_gates}
    else:
        failed_gate_names = set()
    return bool(
        metrics.get("candidate_status") == "prerequisite"
        and "target_behavior_delta" in failed_gate_names
    )


def _verified_prerequisite_composition_rationale(
    repair_focus: Mapping[str, object],
) -> str:
    """Record the materialized composition instead of untrusted model claims."""

    package = repair_focus.get("repair_candidate_package")
    candidate_id = (
        package.get("candidate_id")
        if isinstance(package, Mapping)
        else None
    )
    suffix = (
        f" {candidate_id}"
        if isinstance(candidate_id, str) and candidate_id.strip()
        else ""
    )
    return (
        "Composed a releasable target-behavior delta over verified "
        f"evaluation-support prerequisite{suffix}; inherited candidate-owned "
        "support files byte-for-byte."
    )


def _candidate_output_representation(output: Any) -> CandidateRepresentation:
    if not isinstance(output, Mapping):
        return CandidateRepresentation.FULL_CONTENT
    if isinstance(output.get("patch_intent"), Mapping):
        return CandidateRepresentation.PATCH_INTENT
    if isinstance(output.get("content"), str) and output.get("content"):
        return CandidateRepresentation.FULL_CONTENT
    raw_files = output.get("files")
    if isinstance(raw_files, (list, tuple)) and raw_files:
        return CandidateRepresentation.FILES_ONLY
    return CandidateRepresentation.CANDIDATE_PACKAGE


def _candidate_semantic_error(
    error: ValueError,
    *,
    output: Any,
    request: OptimizerRequest,
) -> CandidateSemanticValidationError:
    if isinstance(error, CandidateSemanticValidationError):
        return error
    if isinstance(error, CandidateMaterializationError):
        code = error.code.value
        field_path = error.field_path.value
    else:
        code = CandidateMaterializationCode.INVALID.value
        field_path = CandidateFailureField.CANDIDATE.value
    return CandidateSemanticValidationError(
        code,
        str(error),
        field_path=field_path,
        representation=_candidate_output_representation(output).value,
        repairable=True,
        allowed_improvement_signal_ids=exposed_improvement_signal_ids(request),
    )


def _overlay_repair_focus_files(
    request: OptimizerRequest,
    *,
    candidate_index: int,
    candidate_files: tuple[CandidateFileDelta, ...],
) -> tuple[tuple[CandidateFileDelta, ...], int]:
    """Apply a repair response as a delta over its focused candidate package."""

    context = request.evolution_context or compile_evolution_context(request)
    files_authorized_by_requirements = bool(request.replay_requirements)
    repair_focus = context.repair_focus_for_candidate(
        candidate_index=candidate_index
    )
    if not isinstance(repair_focus, Mapping):
        return (
            candidate_files if files_authorized_by_requirements else (),
            0,
        )
    package = repair_focus.get("repair_candidate_package")
    raw_files = package.get("files") if isinstance(package, Mapping) else None
    if not isinstance(raw_files, list):
        return (
            candidate_files if files_authorized_by_requirements else (),
            0,
        )

    base_files = validate_candidate_files(
        CandidateFileDelta(
            path=str(item.get("path") or ""),
            operation=str(item.get("operation") or "upsert"),
            content=(
                item.get("content")
                if isinstance(item.get("content"), str)
                else None
            ),
            executable=bool(item.get("executable", False)),
        )
        for item in raw_files
        if isinstance(item, Mapping)
    )
    if (
        _repair_feedback_reached_judged_task_output(repair_focus)
        or _repair_feedback_is_prerequisite_composition(repair_focus)
    ):
        # Judge-stage repair is a target-behavior delta over a runtime that has
        # already passed authoritative replay. Ignore model-proposed harness
        # changes and carry the verified candidate-owned files byte-for-byte,
        # including when the verified package intentionally owns no replay files.
        return base_files, len(base_files)
    if not base_files:
        return (
            candidate_files if files_authorized_by_requirements else (),
            0,
        )
    replacements = {item.path: item for item in candidate_files}
    inherited = sum(1 for item in base_files if item.path not in replacements)
    merged = {
        item.path: replacements.pop(item.path, item)
        for item in base_files
    }
    merged.update(replacements)
    return validate_candidate_files(merged.values()), inherited


def _preserve_existing_replay_package_for_target_delta(
    request: OptimizerRequest,
    *,
    candidate_index: int,
    candidate_files: tuple[CandidateFileDelta, ...],
) -> tuple[tuple[CandidateFileDelta, ...], int]:
    """Preserve authorized candidate files as part of the atomic release package.

    ``_overlay_repair_focus_files`` is the authorization boundary. Once a file
    survives it, silently dropping that file would make replay verify a different
    package from the one the mutator proposed and can leave published Markdown
    with unresolved dependencies. Keep the legacy return shape for diagnostic
    compatibility; the discarded count is now always zero.
    """

    del request, candidate_index
    return candidate_files, 0


def _has_lesson_backed_delta_signal(request: OptimizerRequest) -> bool:
    if request.lesson_records or request.validation_feedback or request.prior_feedback:
        return True
    if request.trainable_cases:
        return True
    return any(pack.steps for pack in request.trace_packs)


def _candidate_strategy_record(
    request: OptimizerRequest,
    *,
    candidate_index: int,
    addressed_signal_ids: tuple[str, ...],
) -> dict[str, Any]:
    population_strategy = _population_strategy(request, candidate_index)
    addressed_lessons = _addressed_lesson_ids(request)
    exposed_signals = exposed_improvement_signal_ids(request)
    preserved_success_behaviors = _preserved_success_behaviors(request)
    risk_notes = _risk_notes(request)
    strategy_hints = _strategy_hints(request)
    record = {
        "strategy_id": f"{population_strategy['name']}:{candidate_index}",
        "candidate_family": population_strategy["name"],
        "intended_behavior_delta": population_strategy["instruction"],
        "addressed_lessons": list(addressed_lessons),
        "exposed_improvement_signals": list(exposed_signals),
        "addressed_improvement_signals": list(
            addressed_signal_ids
        ),
        "harness_diagnostics_considered": list(_harness_diagnostic_ids(request)),
        "preserved_success_behaviors": preserved_success_behaviors,
        "risk_notes": risk_notes,
        "strategy_hints": strategy_hints,
        "replay_priority": _replay_priority(
            addressed_lessons=addressed_lessons,
            preserved_success_behaviors=preserved_success_behaviors,
            risk_notes=risk_notes,
        ),
    }
    context = request.evolution_context or compile_evolution_context(request)
    repair_conformance = context.to_prompt_payload(
        candidate_index=candidate_index
    ).get("repair_conformance")
    if isinstance(repair_conformance, Mapping):
        record["repair_conformance"] = dict(repair_conformance)
    return record


def _candidate_strategy_id(
    request: OptimizerRequest,
    candidate_index: int,
) -> str:
    strategy = _population_strategy(request, candidate_index)
    return f"{strategy['name']}:{candidate_index}"


def _candidate_structural_edit_intent(
    output: Any,
    *,
    base_content: str,
    candidate_content: str,
) -> SkillStructuralEditIntent | None:
    payload = output
    if isinstance(payload, Mapping):
        expected_output = payload.get("expected_output")
        if isinstance(expected_output, Mapping):
            normalized = dict(expected_output)
            for key, value in payload.items():
                if key != "expected_output":
                    normalized.setdefault(key, value)
            payload = normalized
    patch_intent = (
        payload.get("patch_intent")
        if isinstance(payload, Mapping)
        else None
    )
    if not isinstance(patch_intent, Mapping):
        return None
    try:
        return build_skill_structural_edit_intent(
            original_content=base_content,
            candidate_content=candidate_content,
            patch_intent=patch_intent,
        )
    except ValueError:
        # Non-Markdown target adapters can still use patch materialization.
        # Skill local gates fail closed before auto-apply when the typed,
        # content-addressed authorization cannot be constructed.
        return None


def _population_strategy(
    request: OptimizerRequest,
    candidate_index: int,
) -> dict[str, str]:
    context = request.evolution_context or compile_evolution_context(request)
    names = context.population_strategies or ("minimal_behavior_delta",)
    name = names[candidate_index % len(names)]
    instructions = {
        "minimal_behavior_delta": (
            "preserve existing strengths and add the smallest behavior change that "
            "satisfies the typed acceptance constraints"
        ),
        "target_behavior_composition": (
            "inherit the verified evaluation-support package byte-for-byte and "
            "add the smallest releasable target behavior improvement; preserve "
            "successful completion, allow at most one materially different bounded "
            "fallback, then immediately return supported claims or explicit insufficiency"
        ),
        "missing_capability_completion": (
            "publish candidate-owned files that satisfy every applicable capability "
            "authoring contract"
        ),
        "quality_regression_repair": (
            "repair the typed failed gates and required behaviors without unrelated scope"
        ),
        "evidence_quality_repair": (
            "preserve the verified parent behavior and repair only candidate-owned "
            "typed evidence constraints: register or correct artifact references, "
            "support claims from bounded canonical evidence, or omit unsupported claims"
        ),
        "efficiency_and_robustness": (
            "improve reliability and resource economy while preserving required quality"
        ),
    }
    return {
        "name": name,
        "instruction": instructions[name],
    }


def _preserved_success_behaviors(request: OptimizerRequest) -> list[str]:
    behaviors: list[str] = []
    for lesson in request.lesson_records:
        if lesson.lesson_type not in {"lean_solution_path", "trajectory_success_memory", "success_memory"}:
            continue
        if lesson.summary:
            behaviors.append(lesson.summary)
        tool_names = lesson.metrics.get("tool_names") if isinstance(lesson.metrics, Mapping) else None
        if isinstance(tool_names, list) and tool_names:
            behaviors.append("preserve tool path: " + ", ".join(str(item) for item in tool_names[:4]))
    return list(dict.fromkeys(behaviors))[:6]


def _risk_notes(request: OptimizerRequest) -> list[str]:
    notes: list[str] = []
    for lesson in request.lesson_records:
        if lesson.lesson_type in {"failure_memory", "trajectory_failure_memory", "harness_diagnostic"}:
            notes.append(lesson.summary)
        failed_gates = lesson.metrics.get("failed_gates") if isinstance(lesson.metrics, Mapping) else None
        if isinstance(failed_gates, list):
            notes.extend(str(item) for item in failed_gates[:4])
    return list(dict.fromkeys(note for note in notes if note))[:6]


def _harness_diagnostic_ids(request: OptimizerRequest) -> tuple[str, ...]:
    return tuple(
        lesson.lesson_id
        for lesson in request.lesson_records
        if lesson.lesson_type == "harness_diagnostic" and lesson.lesson_id
    )


def _strategy_hints(request: OptimizerRequest) -> list[str]:
    hints: list[str] = []
    for lesson in request.lesson_records:
        if lesson.lesson_type != "harness_diagnostic":
            continue
        metrics = lesson.metrics if isinstance(lesson.metrics, Mapping) else {}
        diagnostic_kind = str(metrics.get("diagnostic_kind") or "").strip()
        if diagnostic_kind == "artifact_lifecycle":
            hints.append("improve artifact lifecycle handling without copying diagnostic labels into runtime instructions")
        elif diagnostic_kind == "workflow":
            hints.append("stabilize replay workflow before adding runtime behavior")
        elif diagnostic_kind == "evaluation":
            hints.append("make evaluator-facing evidence easier to verify without changing task-specific behavior")
        elif diagnostic_kind:
            hints.append(f"consider {diagnostic_kind} as a framework diagnostic, not runtime wording")
    return list(dict.fromkeys(hints))[:6]


def _replay_priority(
    *,
    addressed_lessons: tuple[str, ...],
    preserved_success_behaviors: list[str],
    risk_notes: list[str],
) -> str:
    if addressed_lessons and preserved_success_behaviors:
        return "high"
    if addressed_lessons or risk_notes:
        return "medium"
    return "low"


def _materialize_mutator_output(
    output: Any,
    *,
    request: OptimizerRequest,
    candidate_index: int = 0,
) -> tuple[str, str, str, tuple[CandidateFileDelta, ...]]:
    # Current content remains the default patch base. A complete judge-scored
    # package is the exception: it crossed authoritative replay, so a repair
    # patch must retain its positive target delta.
    base_content = _focused_repair_patch_base(
        request,
        candidate_index=candidate_index,
    )
    if isinstance(output, Mapping):
        # Some structured-output providers return the schema payload under an
        # ``expected_output`` envelope even though the prompt requests the
        # value itself.  Unwrap that provider-level envelope at the framework
        # boundary so a valid candidate is not silently discarded; preserve
        # any top-level fields as fallbacks for providers that split metadata
        # between the envelope and the outer object.
        expected_output = output.get("expected_output")
        if isinstance(expected_output, Mapping):
            normalized_output = dict(expected_output)
            for key, value in output.items():
                if key != "expected_output":
                    normalized_output.setdefault(key, value)
            output = normalized_output
        content = output.get("content")
        patch_intent = output.get("patch_intent")
        rationale = output.get("rationale", "")
        raw_files = output.get("files", ())
    else:
        content = output
        patch_intent = None
        rationale = ""
        raw_files = ()
    if isinstance(patch_intent, Mapping):
        try:
            content = apply_skill_patch_intent(base_content, patch_intent)
            materialization = "patch_intent"
        except CandidateMaterializationError as exc:
            # A repair candidate can legitimately be a package-file-only delta.
            # If its optional Markdown patch refers to a section that does not
            # exist in the authoritative target snapshot, retain the snapshot
            # and continue only when the response also carries candidate-owned
            # files.  Other patch failures remain fail-closed, and downstream
            # package/reference/conformance gates still validate the file delta.
            has_file_delta = isinstance(raw_files, (list, tuple)) and any(
                isinstance(item, Mapping) for item in raw_files
            )
            if (
                exc.code is CandidateMaterializationCode.PATCH_SECTION_NOT_FOUND
                and has_file_delta
            ):
                content = base_content
                materialization = "files_only_patch_fallback"
            else:
                raise
    else:
        materialization = "full_content"
    if not isinstance(content, str) or not content:
        if isinstance(raw_files, (list, tuple)) and any(
            isinstance(item, Mapping) for item in raw_files
        ):
            content = base_content
            materialization = "files_only"
        else:
            raise CandidateMaterializationError(
                CandidateMaterializationCode.CONTENT_REQUIRED,
                "mutator output must include content, patch_intent, or package files",
                field_path=CandidateFailureField.CONTENT,
            )
    if _focused_source_repair_parent_content_required(
        request,
        candidate_index=candidate_index,
    ):
        # Pure compiler/runtime conformance repair owns only typed support
        # branches. A task-rollout contract that names SKILL.md instead owns
        # target behavior and must preserve the model's semantic content delta.
        content = base_content
        materialization = f"{materialization}+focused_parent_content"
    if not isinstance(rationale, str):
        rationale = ""
    if not isinstance(raw_files, (list, tuple)):
        raise CandidateMaterializationError(
            CandidateMaterializationCode.FILES_TYPE_INVALID,
            "mutator files must be a list",
            field_path=CandidateFailureField.FILES,
        )
    files = validate_candidate_files(
        CandidateFileDelta(
            path=str(item.get("path") or ""),
            operation=str(item.get("operation") or "upsert"),
            content=(
                item.get("content")
                if isinstance(item.get("content"), str)
                else None
            ),
            executable=bool(item.get("executable", False)),
        )
        for item in raw_files
        if isinstance(item, Mapping)
    )
    if materialization.startswith("files_only") and not files:
        raise CandidateMaterializationError(
            CandidateMaterializationCode.FILES_ONLY_DELTA_REQUIRED,
            "files-only mutator output must include a valid file delta",
            field_path=CandidateFailureField.FILES,
        )
    return content, rationale, materialization, files


def _focused_repair_patch_base(
    request: OptimizerRequest,
    *,
    candidate_index: int,
) -> str:
    context = request.evolution_context or compile_evolution_context(request)
    repair_focus = context.repair_focus_for_candidate(
        candidate_index=candidate_index
    )
    if not isinstance(repair_focus, Mapping):
        return request.current_content
    package = repair_focus.get("repair_candidate_package")
    parent_content = (
        package.get("content") if isinstance(package, Mapping) else None
    )
    raw_files = package.get("files") if isinstance(package, Mapping) else None
    source_repair = _focused_source_repair_parent_content_required(
        request,
        candidate_index=candidate_index,
    )
    content_limit = 32_000 if raw_files else 64_000
    if (
        not isinstance(parent_content, str)
        or not parent_content.strip()
        or (not source_repair and len(parent_content) >= content_limit)
    ):
        return request.current_content
    if source_repair or _repair_feedback_reached_judged_task_output(
        repair_focus
    ):
        return parent_content
    return request.current_content


def _focused_source_repair_parent_content_required(
    request: OptimizerRequest,
    *,
    candidate_index: int,
) -> bool:
    context = request.evolution_context or compile_evolution_context(request)
    repair_focus = context.repair_focus_for_candidate(
        candidate_index=candidate_index
    )
    if not isinstance(repair_focus, Mapping):
        return False
    if (
        _repair_feedback_reached_judged_task_output(repair_focus)
        or _repair_feedback_is_prerequisite_composition(repair_focus)
    ):
        return False
    package = repair_focus.get("repair_candidate_package")
    if not isinstance(package, Mapping):
        return False
    contract = compile_repair_conformance_contract(repair_focus)
    return bool(
        contract is not None
        and contract.required_branch_paths
        and "SKILL.md" not in contract.required_branch_paths
    )


def _candidate_id(
    request: OptimizerRequest,
    content: str,
    *,
    files: tuple[CandidateFileDelta, ...] = (),
    index: int,
) -> str:
    file_payload = [
        (item.path, item.operation, item.content, item.executable)
        for item in validate_candidate_files(files)
    ]
    digest = hashlib.sha256(
        json.dumps(
            {
                "target_type": request.target.target_type,
                "target_id": request.target.target_id,
                "index": index,
                "content": content,
                "files": file_payload,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"llm-mutator-{digest}"


def _content_fingerprint(content: str) -> str:
    normalized = "\n".join(line.rstrip() for line in content.strip().splitlines())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _violates_transport_completion_invariant(content: str) -> bool:
    """Reject explicit policies that equate a first transport result with task completion."""

    normalized = " ".join(content.lower().split())
    direct_completion_rules = (
        r"\bfirst successful\b.{0,160}\b(?:response|extraction)\b"
        r".{0,160}\b(?:treat|consider|mark)\b.{0,100}\bcomplete\b",
        r"\bfirst successful\b.{0,160}\b(?:response|extraction)\b"
        r".{0,100}\bcompletion signal\b",
        r"\brequested output can be produced from the first successful\b",
    )
    direct_violation = any(
        re.search(pattern, normalized) for pattern in direct_completion_rules
    )

    semantic_guards = (
        "transport completion is necessary but not sufficient",
        "transport completion is not task completion",
        "payload directly supports the requested claims",
        "payload directly support the requested claims",
        "payload directly supports the user's requested result",
        "payload directly supports the user’s requested result",
        "verify task semantic sufficiency",
    )
    if direct_violation and not any(
        guard in normalized for guard in semantic_guards
    ):
        return True

    evidence_workflow = all(
        token in normalized for token in ("evidence", "artifact", "claim")
    ) and any(
        token in normalized
        for token in (
            "continue acquisition",
            "continue with one",
            "try one materially different",
            "retry once",
        )
    )
    hard_stop = (
        "exactly one materially different bounded" in normalized
        and "do not issue more tool calls after that single fallback" in normalized
    )
    return evidence_workflow and not hard_stop


def _append_transport_completion_invariant(content: str) -> str:
    return (
        content.rstrip()
        + "\n\n## Task Semantic Completion Invariant\n\n"
        "This invariant overrides any earlier completion rule in this skill. A successful "
        "handshake, HTTP status, structured envelope, metadata record, tool-execution "
        "summary, or first data-plane response is a delivery signal, not task completion. "
        "Persist the first usable response immediately, then verify claim by claim that its "
        "payload directly supports the user's requested result. Stop only when that semantic "
        "check passes. If it does not, make exactly one materially different bounded "
        "artifact-backed attempt, then immediately return either the "
        "supported answer or an explicit insufficiency. Do not issue more tool calls after "
        "that single fallback. Evidence artifacts must contain direct tool or primary-source "
        "output, never a model-authored draft answer, synthesis, inferred comparison, or "
        "remembered knowledge written to a file and cited back as support. Add a manifest "
        "entry only for a real captured source, read that source before answering, and bind "
        "each cited claim to the exact bounded excerpt or structured fields used. Never "
        "claim artifact or manifest-entry counts without computing them from the final "
        "manifest. Before finalizing, remove every factual, configuration, "
        "bibliographic, quantitative, symbolic, and quoted claim that is not directly "
        "supported by a bounded non-compacted artifact excerpt or structured field. Do not "
        "expand evidence with remembered details. When no supporting source artifact exists, "
        "state the insufficiency instead of supplying examples, citations, parameter values, "
        "equations, or other missing content.\n"
    )


def _lesson_set_fingerprint(request: OptimizerRequest) -> str | None:
    lesson_ids = _addressed_lesson_ids(request)
    if not lesson_ids:
        return None
    return hashlib.sha256("\n".join(lesson_ids).encode("utf-8")).hexdigest()


def _addressed_lesson_ids(request: OptimizerRequest) -> tuple[str, ...]:
    lesson_ids: list[str] = [
        lesson.lesson_id
        for lesson in request.lesson_records
        if lesson.lesson_id
    ]
    for feedback in (*request.validation_feedback, *request.prior_feedback):
        summary = normalize_feedback_summary(feedback)
        metrics = summary.get("metrics")
        if not isinstance(metrics, Mapping):
            continue
        lesson_id = metrics.get("lesson_id")
        if isinstance(lesson_id, str) and lesson_id:
            lesson_ids.append(lesson_id)
    return tuple(dict.fromkeys(lesson_ids))


@dataclass(frozen=True)
class _CandidatePolicyAssessment:
    policy_id: str
    enforcement: str
    reason_codes: tuple[str, ...]
    constraint_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "enforcement": self.enforcement,
            "reason_codes": list(self.reason_codes),
            "constraint_ids": list(self.constraint_ids),
        }


def _active_frontier_key(
    request: OptimizerRequest,
    candidate_index: int,
) -> str | None:
    if candidate_index >= len(request.active_repair_frontier_keys):
        return None
    value = request.active_repair_frontier_keys[candidate_index]
    return value if isinstance(value, str) and value else None


def _feedback_semantic_keys(feedback: object) -> tuple[str, ...]:
    metrics = getattr(feedback, "metrics", None)
    if not isinstance(metrics, Mapping):
        return ()
    values: list[str] = []
    direct = metrics.get("causal_semantic_key")
    if isinstance(direct, str) and direct:
        values.append(direct)
    events = metrics.get("causal_failure_events")
    if isinstance(events, (list, tuple)):
        for event in events:
            if not isinstance(event, Mapping):
                continue
            semantic_key = event.get("semantic_key")
            if isinstance(semantic_key, str) and semantic_key:
                values.append(semantic_key)
    return tuple(dict.fromkeys(values))


def _scoped_policy_feedback(
    request: OptimizerRequest,
    *,
    candidate_index: int,
) -> tuple[object, ...]:
    """Bind historical policy signals to the scheduler's active frontier."""

    active_frontier = _active_frontier_key(request, candidate_index)
    current = tuple(request.validation_feedback)
    if active_frontier is None:
        # Current-run feedback is causally local. Historical feedback without a
        # scheduler binding must not globally activate a generation policy.
        return current
    scoped: list[object] = []
    for feedback in (*current, *request.prior_feedback):
        semantic_keys = _feedback_semantic_keys(feedback)
        if active_frontier in semantic_keys or (
            feedback in current and not semantic_keys
        ):
            scoped.append(feedback)
    return tuple(scoped)


def _request_has_high_baseline_regression(
    request: OptimizerRequest,
    *,
    candidate_index: int = 0,
) -> bool:
    for feedback in _scoped_policy_feedback(
        request,
        candidate_index=candidate_index,
    ):
        raw_metrics = getattr(feedback, "metrics", None)
        raw_diagnostics = (
            raw_metrics.get("candidate_validation_diagnostics")
            if isinstance(raw_metrics, Mapping)
            else None
        )
        if isinstance(raw_diagnostics, (list, tuple)) and any(
            isinstance(item, Mapping)
            and item.get("policy_id") == "preserve_high_baseline"
            and item.get("enforcement") == "hard"
            for item in raw_diagnostics
        ):
            return True
        summary = normalize_feedback_summary(feedback)
        metrics = summary.get("metrics")
        metrics = metrics if isinstance(metrics, Mapping) else {}
        baseline_score = _metric_float(metrics.get("baseline_score"))
        candidate_score = _metric_float(metrics.get("candidate_score"))
        score_delta = _metric_float(metrics.get("score_delta"))
        if baseline_score is None or baseline_score < 85.0:
            continue
        if score_delta is not None and score_delta <= 0:
            return True
        if candidate_score is not None and candidate_score <= baseline_score:
            return True

        required_behaviors = _string_set(summary.get("required_behaviors"))
        if required_behaviors & {
            "differentiate_from_high_scoring_baseline",
            "preserve_baseline_strengths",
            "define_behavior_delta_before_tools",
            "prefer_targeted_changes_over_broad_rewrites",
        }:
            return True

        repair_plan = summary.get("repair_plan")
        if isinstance(repair_plan, Mapping) and _string_set(repair_plan.get("actions")) & {
            "preserve_high_scoring_baseline_strengths",
            "define_candidate_behavior_delta",
            "prefer_targeted_change_over_broad_rewrite",
        }:
            return True
    return False


def _focused_repair_base_content(
    request: OptimizerRequest,
    *,
    candidate_index: int,
) -> str | None:
    context = request.evolution_context or compile_evolution_context(request)
    focus = context.repair_focus_for_candidate(candidate_index=candidate_index)
    package = (
        focus.get("repair_candidate_package")
        if isinstance(focus, Mapping)
        else None
    )
    content = package.get("content") if isinstance(package, Mapping) else None
    return content if isinstance(content, str) and content.strip() else None


def _high_baseline_policy_assessment(
    content: str,
    *,
    current_content: str,
    request: OptimizerRequest,
    candidate_index: int,
) -> _CandidatePolicyAssessment | None:
    if not _request_has_high_baseline_regression(
        request,
        candidate_index=candidate_index,
    ):
        return None

    retained_delta = _retained_baseline_delta(
        content,
        current_content=current_content,
    )
    text = (retained_delta if retained_delta is not None else content).lower()
    focused_base = _focused_repair_base_content(
        request,
        candidate_index=candidate_index,
    )
    if (
        focused_base is not None
        and focused_base.rstrip() != current_content.rstrip()
        and content.rstrip().startswith(focused_base.rstrip())
        and not content.rstrip().startswith(current_content.rstrip())
    ):
        return _CandidatePolicyAssessment(
            policy_id="preserve_high_baseline",
            enforcement="hard",
            reason_codes=("authoritative_base_replaced_by_rejected_parent",),
            constraint_ids=("preserve_authoritative_current_base",),
        )

    has_preserve = bool(
        re.search(
            r"\b(preserve|keep|unchanged|baseline strengths|baseline behavior|保留|保持|不变)\b",
            text,
        )
    )
    has_behavior_delta = bool(
        re.search(
            r"\b(behavior delta|delta|change only|only add|only change|small targeted|"
            r"targeted change|行为增量|执行行为|只改变|仅新增)\b",
            text,
        )
    )
    has_acceptance_check = bool(
        re.search(
            r"\b(acceptance check|acceptance criteria|must beat|must pass|verify|"
            r"verification|no worse than|验收|准入|验证|检查)\b",
            text,
        )
    )
    if has_preserve and has_behavior_delta and has_acceptance_check:
        if retained_delta is not None or _preserves_lean_solution_path(text, request):
            return None
        return _CandidatePolicyAssessment(
            policy_id="preserve_high_baseline",
            enforcement="heuristic",
            reason_codes=("lean_solution_path_not_explicitly_preserved",),
            constraint_ids=("preserve_lean_solution_path",),
        )

    growth_ratio = len(content) / max(len(current_content), 1)
    broad_terms = (
        "comprehensive",
        "broader",
        "more evidence",
        "collect more",
        "expand",
        "always",
        "all claims",
        "全面",
        "更多证据",
        "扩大",
    )
    has_broad_guidance = _has_unnegated_guidance(text, broad_terms)
    reasons: list[str] = []
    constraints: list[str] = []
    if has_broad_guidance:
        reasons.append("broad_evidence_expansion_language")
        constraints.append("avoid_broad_evidence_expansion")
    if retained_delta is not None:
        max_delta_chars = min(4_000, max(1_200, int(len(current_content) * 0.4)))
        if len(retained_delta) > max_delta_chars:
            reasons.append("target_delta_size_exceeds_heuristic_bound")
            constraints.append("bound_target_delta_size")
    else:
        if growth_ratio > 1.4:
            reasons.append("candidate_growth_exceeds_heuristic_ratio")
            constraints.append("bound_target_delta_size")
        if not _preserves_lean_solution_path(text, request):
            reasons.append("lean_solution_path_not_explicitly_preserved")
            constraints.append("preserve_lean_solution_path")
    if not reasons:
        return None
    return _CandidatePolicyAssessment(
        policy_id="preserve_high_baseline",
        enforcement="heuristic",
        reason_codes=tuple(dict.fromkeys(reasons)),
        constraint_ids=tuple(dict.fromkeys(constraints)),
    )


def _is_weak_high_baseline_regression_candidate(
    content: str,
    *,
    current_content: str,
    request: OptimizerRequest | None = None,
) -> bool:
    """Compatibility predicate: heuristic risk is observable, not a hard veto."""

    if request is None:
        return False
    return _high_baseline_policy_assessment(
        content,
        current_content=current_content,
        request=request,
        candidate_index=0,
    ) is not None


def _policy_affected_case_ids(
    request: OptimizerRequest,
    *,
    candidate_index: int,
) -> tuple[str, ...]:
    case_ids: list[str] = []
    for feedback in _scoped_policy_feedback(
        request,
        candidate_index=candidate_index,
    ):
        metrics = getattr(feedback, "metrics", None)
        if not isinstance(metrics, Mapping):
            continue
        raw_case_ids = metrics.get("affected_case_ids")
        if isinstance(raw_case_ids, (list, tuple)):
            case_ids.extend(
                str(item) for item in raw_case_ids if isinstance(item, str) and item
            )
        raw_events = metrics.get("causal_failure_events")
        if not isinstance(raw_events, (list, tuple)):
            continue
        for event in raw_events:
            event_case_ids = (
                event.get("affected_case_ids")
                if isinstance(event, Mapping)
                else None
            )
            if isinstance(event_case_ids, (list, tuple)):
                case_ids.extend(
                    str(item)
                    for item in event_case_ids
                    if isinstance(item, str) and item
                )
    return tuple(dict.fromkeys(case_ids))


def _retained_baseline_delta(
    content: str,
    *,
    current_content: str,
) -> str | None:
    baseline = current_content.rstrip()
    if not baseline or not content.startswith(baseline):
        return None
    return content[len(baseline) :].strip()


def _has_unnegated_guidance(text: str, terms: tuple[str, ...]) -> bool:
    for term in terms:
        for match in re.finditer(re.escape(term), text):
            clause_start = max(
                text.rfind(delimiter, 0, match.start())
                for delimiter in ("\n", ".", ";", "!", "?", "。", "；", "！", "？")
            )
            prefix = text[clause_start + 1 : match.start()]
            if re.search(
                r"(?:\b(?:do not|don't|never|avoid|without|not)\b|"
                r"不要|不得|避免|禁止|无需|不再)",
                prefix,
            ):
                continue
            return True
    return False


def _preserves_lean_solution_path(
    lowered_content: str,
    request: OptimizerRequest | None,
) -> bool:
    if request is None:
        return True
    lean_lessons = [
        lesson
        for lesson in request.lesson_records
        if lesson.lesson_type == "lean_solution_path"
    ]
    if not lean_lessons:
        return True
    generic_lean_markers = (
        "lean path",
        "lean successful path",
        "shortest path",
        "single artifact",
        "one artifact",
        "preserve successful",
        "preserve lean",
    )
    if any(marker in lowered_content for marker in generic_lean_markers):
        return True
    tool_names = {
        str(tool_name).strip().lower()
        for lesson in lean_lessons
        if isinstance(lesson.metrics, Mapping)
        for tool_name in (
            lesson.metrics.get("tool_names")
            if isinstance(lesson.metrics.get("tool_names"), list)
            else []
        )
        if str(tool_name).strip()
    }
    return bool(tool_names and any(tool_name in lowered_content for tool_name in tool_names))


def _metric_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}
