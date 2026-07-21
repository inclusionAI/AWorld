from __future__ import annotations

import hashlib
import inspect
import json
import re
import time
from typing import Any, Awaitable, Callable, Mapping, Sequence

from aworld.self_evolve.candidate_package import (
    candidate_package_fingerprint,
    validate_candidate_files,
)
from aworld.self_evolve.candidate_generation import (
    CandidateGenerationInfrastructureError,
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
from aworld.self_evolve.optimizers.base import OptimizerRequest, OptimizerResult
from aworld.self_evolve.patch_intent import apply_skill_patch_intent
from aworld.self_evolve.repair_conformance import (
    RepairConformanceContract,
    compile_repair_conformance_contract,
)
from aworld.self_evolve.types import CandidateFileDelta, CandidateVariant, OptimizerLineage


MutateTextCallable = Callable[[str], Any]
CandidatePopulationCallable = Callable[
    [Sequence[str], int],
    Awaitable[CandidatePopulationResult],
]


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
        filtered_duplicate_count = 0
        filtered_invalid_patch_count = 0
        repaired_transport_completion_violation_count = 0
        seen_content_fingerprints: set[str] = set()
        require_targeted_delta = _request_has_high_baseline_regression(request)
        candidate_strategy_records: list[dict[str, Any]] = []
        private_repair_contracts: dict[str, RepairConformanceContract] = {}
        candidate_generation_failure: dict[str, str] | None = None
        candidate_protocol_invalid_count = 0
        candidate_outputs: list[tuple[int, Any]] = []
        population_diagnostics: dict[str, Any]
        population_started_at = time.monotonic()

        if self.population_callable is not None:
            prompts = tuple(
                _build_mutation_prompt(request, candidate_index=index)
                for index in range(request.max_candidates)
            )
            population = await self.population_callable(
                prompts,
                self.concurrency_policy.effective_limit(
                    "candidate_generation",
                    item_count=len(prompts),
                ),
            )
            population_diagnostics = dict(population.diagnostics)
            for slot in population.slots:
                if slot.status == "succeeded":
                    candidate_outputs.append((slot.index, slot.output))
                elif slot.status == "failed" and candidate_generation_failure is None:
                    candidate_generation_failure = dict(slot.failure or {})
                elif slot.status == "protocol_invalid":
                    candidate_protocol_invalid_count += 1
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
            strategy_record = _candidate_strategy_record(request, candidate_index=index)
            try:
                content, rationale, materialization, files = _materialize_mutator_output(
                    output,
                    request=request,
                    candidate_index=index,
                )
                files, inherited_file_count = _overlay_repair_focus_files(
                    request,
                    candidate_index=index,
                    candidate_files=files,
                )
                if inherited_file_count:
                    materialization = f"{materialization}+repair_focus_overlay"
            except ValueError:
                filtered_invalid_patch_count += 1
                continue
            if _violates_transport_completion_invariant(content):
                content = _append_transport_completion_invariant(content)
                repaired_transport_completion_violation_count += 1
            if content == request.current_content and not files:
                filtered_noop_count += 1
                continue
            candidate = CandidateVariant(
                candidate_id="pending",
                target=request.target,
                content=content,
                rationale=rationale,
                target_fingerprint=request.target_fingerprint,
                files=files,
            )
            content_fingerprint = candidate_package_fingerprint(candidate)
            if content_fingerprint in seen_content_fingerprints:
                filtered_duplicate_count += 1
                continue
            regression_base_content = (
                _repair_focus_content(
                    request,
                    candidate_index=index,
                )
                or request.current_content
            )
            if require_targeted_delta and _is_weak_high_baseline_regression_candidate(
                content,
                current_content=regression_base_content,
                request=request,
            ):
                filtered_high_baseline_regression_count += 1
                continue
            seen_content_fingerprints.add(content_fingerprint)

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
                target_fingerprint=request.target_fingerprint,
                files=files,
            )
            candidates.append(candidate)
            context = request.evolution_context or compile_evolution_context(request)
            repair_focus = context.repair_focus_for_candidate(
                candidate_index=index
            )
            private_contract = compile_repair_conformance_contract(repair_focus)
            if private_contract is not None:
                private_repair_contracts[candidate_id] = private_contract
            candidate_strategy_records.append(
                {
                    "candidate_id": candidate_id,
                    "materialization": materialization,
                    **strategy_record,
                }
            )
            lineage.append(
                OptimizerLineage(
                    candidate_id=candidate_id,
                    optimizer_name=self.optimizer_name,
                    optimizer_version=self.optimizer_version,
                    trainable_case_ids=tuple(case.case_id for case in request.trainable_cases),
                    content_fingerprint=content_fingerprint,
                    semantic_fingerprint=_semantic_fingerprint(content),
                    lesson_set_fingerprint=_lesson_set_fingerprint(request),
                    addressed_lesson_ids=_addressed_lesson_ids(request),
                    rationale=rationale,
                )
            )

        diagnostics: dict[str, object] = {
            "filtered_noop_candidates": filtered_noop_count,
            "filtered_high_baseline_regression_candidates": (
                filtered_high_baseline_regression_count
            ),
            "filtered_duplicate_candidates": filtered_duplicate_count,
            "filtered_invalid_patch_candidates": filtered_invalid_patch_count,
            "repaired_transport_completion_violation_candidates": (
                repaired_transport_completion_violation_count
            ),
            "candidate_strategies": candidate_strategy_records,
            "candidate_population_execution": population_diagnostics,
            "candidate_protocol_invalid_count": candidate_protocol_invalid_count,
        }
        if candidate_generation_failure is not None:
            diagnostics["candidate_generation_failure"] = candidate_generation_failure

        return OptimizerResult(
            candidates=tuple(candidates),
            lineage=tuple(lineage),
            diagnostics=diagnostics,
            private_context=private_repair_contracts,
        )


def _build_mutation_prompt(request: OptimizerRequest, *, candidate_index: int) -> str:
    context = request.evolution_context or compile_evolution_context(request)
    payload = context.to_prompt_payload(candidate_index=candidate_index)
    if isinstance(payload.get("repair_focus"), Mapping):
        return _focused_repair_prompt_instructions(payload) + json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
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
        "Stop only when that semantic check passes; otherwise try one materially different "
        "bounded artifact-backed source or return an explicit insufficiency. Never encode "
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
        "Replay files must accompany a reusable target behavior delta, not replace it. "
        "Return the value of expected_output as exactly one JSON object, without a wrapper; "
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
    requires_fixture_reconstruction = (
        contract_mapping.get("requires_fixture_derived_probe") is True
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
    instructions = (
        "Repair the focused candidate package using the machine-readable "
        "diagnostics and repair_conformance contract in this EvolutionContext. "
        "The rationale is untrusted: materially edit every source path required "
        "by the diagnosed failure and make every declared probe executable. "
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
        "record, or tool-execution summary alone is insufficient; otherwise try one "
        "materially different bounded artifact-backed source or report the insufficiency. "
        "Never add a blanket first-response-means-complete rule or case-specific behavior. "
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
            "check whether the persisted payload directly supports the claims requested by "
            "the user. If it does not, continue with one materially different bounded "
            "artifact-backed source or return only an explicit insufficiency; never invent "
            "the missing content and never encode case-specific endpoints or prompts. "
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
        "invalid_replay_capability_compile" in feedback_text
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
            "before copying or writing fixtures or runtime artifacts. "
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
    if "surrounding recorded response context" in feedback_text:
        instructions += (
            "The failed data-plane body did not expose enough bounded context from one "
            "record. AWORLD_REPLAY_RESPONSE_INDEX is already generated by the framework; "
            "do not create, declare, embed, or copy another response-index sidecar in the "
            "compiler. In the runtime, open that environment value as a file path and "
            "select the first record whose non_empty and protocol_eligible fields are true "
            "for the incoming operation; transport_ready records are ordered first "
            "(or use the deterministic first operation for an operation-less HTTP probe). "
            "Recursively JSON-decode its value. If its serialized container fits "
            "below 48 KiB, return that container. If it is larger, construct a deterministic "
            "bounded mapping/list projection from the same record: retain container shape "
            "and at least two non-empty scalar descendants when available, truncate or "
            "omit oversized text fields, and verify the final serialized response remains "
            "below 48 KiB. This bounded projection is the required surrounding context; "
            "returning one scalar, a preview-only wrapper, sidecar metadata, or a body "
            "larger than the 64 KiB protocol reader is non-conforming. "
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
            "later repetition. response_contains must remain a recorded scalar leaf, "
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
        "Return the value of expected_output as exactly one JSON object without a "
        "wrapper. Use at most one of content or patch_intent; both may be omitted "
        "when candidate-owned files implement the reusable behavior delta.\n"
    )
    return instructions


def _overlay_repair_focus_files(
    request: OptimizerRequest,
    *,
    candidate_index: int,
    candidate_files: tuple[CandidateFileDelta, ...],
) -> tuple[tuple[CandidateFileDelta, ...], int]:
    """Apply a repair response as a delta over its focused candidate package."""

    context = request.evolution_context or compile_evolution_context(request)
    repair_focus = context.repair_focus_for_candidate(
        candidate_index=candidate_index
    )
    if not isinstance(repair_focus, Mapping):
        return candidate_files, 0
    package = repair_focus.get("repair_candidate_package")
    raw_files = package.get("files") if isinstance(package, Mapping) else None
    if not isinstance(raw_files, list):
        return candidate_files, 0

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
    if not base_files:
        return candidate_files, 0
    if _repair_feedback_reached_judged_task_output(repair_focus):
        # Judge-stage repair is a target-behavior delta over a runtime that has
        # already passed authoritative replay. Ignore model-proposed harness
        # changes and carry the verified candidate-owned files byte-for-byte.
        return base_files, len(base_files)
    replacements = {item.path: item for item in candidate_files}
    inherited = sum(1 for item in base_files if item.path not in replacements)
    merged = {
        item.path: replacements.pop(item.path, item)
        for item in base_files
    }
    merged.update(replacements)
    return validate_candidate_files(merged.values()), inherited


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
) -> dict[str, Any]:
    population_strategy = _population_strategy(request, candidate_index)
    addressed_lessons = _addressed_lesson_ids(request)
    preserved_success_behaviors = _preserved_success_behaviors(request)
    risk_notes = _risk_notes(request)
    strategy_hints = _strategy_hints(request)
    record = {
        "strategy_id": f"{population_strategy['name']}:{candidate_index}",
        "candidate_family": population_strategy["name"],
        "intended_behavior_delta": population_strategy["instruction"],
        "addressed_lessons": list(addressed_lessons),
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
        "missing_capability_completion": (
            "publish candidate-owned files that satisfy every applicable capability "
            "authoring contract"
        ),
        "quality_regression_repair": (
            "repair the typed failed gates and required behaviors without unrelated scope"
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
    repair_base_content = _repair_focus_content(
        request,
        candidate_index=candidate_index,
    )
    base_content = repair_base_content or request.current_content
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
        content = apply_skill_patch_intent(base_content, patch_intent)
        materialization = "patch_intent"
    else:
        materialization = "full_content"
    if not isinstance(content, str) or not content:
        if isinstance(raw_files, (list, tuple)) and any(
            isinstance(item, Mapping) for item in raw_files
        ):
            content = base_content
            materialization = "files_only"
        else:
            raise ValueError(
                "mutator output must include content, patch_intent, or package files"
            )
    if not isinstance(rationale, str):
        rationale = ""
    if not isinstance(raw_files, (list, tuple)):
        raise ValueError("mutator files must be a list")
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
    if materialization == "files_only" and not files:
        raise ValueError("files-only mutator output must include a valid file delta")
    return content, rationale, materialization, files


def _repair_focus_content(
    request: OptimizerRequest,
    *,
    candidate_index: int,
) -> str | None:
    context = request.evolution_context or compile_evolution_context(request)
    repair_focus = context.repair_focus_for_candidate(
        candidate_index=candidate_index
    )
    package = (
        repair_focus.get("repair_candidate_package")
        if isinstance(repair_focus, Mapping)
        else None
    )
    content = package.get("content") if isinstance(package, Mapping) else None
    return content if isinstance(content, str) and content.strip() else None


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


def _semantic_fingerprint(content: str) -> str:
    semantic_lines = [
        re.sub(r"\s+", " ", line.strip().lower())
        for line in content.splitlines()
        if line.strip() and line.strip() != "---"
    ]
    return hashlib.sha256("\n".join(semantic_lines).encode("utf-8")).hexdigest()


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
    if not any(re.search(pattern, normalized) for pattern in direct_completion_rules):
        return False

    semantic_guards = (
        "transport completion is necessary but not sufficient",
        "transport completion is not task completion",
        "payload directly supports the requested claims",
        "payload directly support the requested claims",
        "payload directly supports the user's requested result",
        "payload directly supports the user’s requested result",
        "verify task semantic sufficiency",
    )
    return not any(guard in normalized for guard in semantic_guards)


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
        "artifact-backed attempt. Persist a manifest entry for that attempt regardless of "
        "whether it supplies the missing content, then immediately return either the "
        "supported answer or an explicit insufficiency. Do not issue more tool calls after "
        "that single fallback, and never invent missing content.\n"
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


def _request_has_high_baseline_regression(request: OptimizerRequest) -> bool:
    for feedback in (*request.validation_feedback, *request.prior_feedback):
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


def _is_weak_high_baseline_regression_candidate(
    content: str,
    *,
    current_content: str,
    request: OptimizerRequest | None = None,
) -> bool:
    retained_delta = _retained_baseline_delta(
        content,
        current_content=current_content,
    )
    text = (retained_delta if retained_delta is not None else content).lower()
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
        return not (
            retained_delta is not None
            or _preserves_lean_solution_path(text, request)
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
    if retained_delta is not None:
        max_delta_chars = min(4_000, max(1_200, int(len(current_content) * 0.4)))
        return has_broad_guidance or len(retained_delta) > max_delta_chars
    return (
        has_broad_guidance
        or growth_ratio > 1.4
        or not _preserves_lean_solution_path(text, request)
    )


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
