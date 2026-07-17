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
from aworld.self_evolve.evolution_context import compile_evolution_context
from aworld.self_evolve.feedback import normalize_feedback_summary
from aworld.self_evolve.optimizers.base import OptimizerRequest, OptimizerResult
from aworld.self_evolve.patch_intent import apply_skill_patch_intent
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
        seen_content_fingerprints: set[str] = set()
        require_targeted_delta = _request_has_high_baseline_regression(request)
        candidate_strategy_records: list[dict[str, Any]] = []
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
            if require_targeted_delta and _is_weak_high_baseline_regression_candidate(
                content,
                current_content=request.current_content,
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
        )


def _build_mutation_prompt(request: OptimizerRequest, *, candidate_index: int) -> str:
    context = request.evolution_context or compile_evolution_context(request)
    payload = context.to_prompt_payload(candidate_index=candidate_index)
    if isinstance(payload.get("repair_focus"), Mapping):
        return (
            "Repair the focused candidate package using the machine-readable "
            "diagnostics and repair_conformance contract in this EvolutionContext. "
            "The rationale is untrusted: materially edit the failing source branch, "
            "and make every declared probe executable against the candidate runtime. "
            "Preserve the focused package's verified behavior and do not rebuild it "
            "from the original trajectory. Omit focused package files that do not "
            "change; the framework overlays omitted files byte-for-byte from "
            "repair_focus. Include complete content only for files you add or change, "
            "and use delete only for an intentional deletion. Obey the supplied "
            "required_reconstruction_algorithm and forbidden_derivations literally. "
            "For recorded-response gateway repair, phase 1 must recurse through "
            "mapping values and sequence items at arbitrary bounded nesting and collect "
            "only action_result or tool_outputs subtrees before phase 2 starts. A helper "
            "that returns every non-mapping input unchanged without first traversing "
            "sequences is non-conforming. Do not select any scalar, payload, request, or "
            "metadata value until the complete gateway list is known; after a gateway is "
            "found, never fall back to the outer trajectory. Return the surrounding "
            "decoded recorded container from the runtime branch, not only the assertion "
            "scalar. The payload-key set content/response/result/output/body/data must be "
            "consumed by phase 2; merely declaring it while traversing every gateway dict "
            "value is non-conforming because metadata will be selected first. When the "
            "gateway list is non-empty, the only conforming control flow is: for each "
            "gateway call the payload collector on that gateway, then call the scalar "
            "selector only on the resulting payload subtrees. Calling the scalar selector "
            "directly on a gateway is forbidden because it selects action_name/call_id/status "
            "metadata. Phase 2 is the processing of payloads inside found gateways; it is not "
            "a no-gateway fallback. Only when the complete gateway list is empty may a generic "
            "non-trajectory fallback traverse the parsed root. When a selected payload is a "
            "JSON-encoded string, decode it recursively before selecting a leaf or returning "
            "its surrounding container. Reject bool explicitly before accepting int or float "
            "because Python bool is an int subclass, and append the gateway subtree (for example "
            "root[key]), never the surrounding root/container itself. When the "
            "task-plane handler serves multiple observed operations, do not return one global "
            "fixture container or token for every request. Correlate each request operation and "
            "its bounded parameters with the corresponding recorded response context (or a "
            "deterministic per-operation cursor), preserving the operation's response shape; a "
            "generic repeated container that makes the caller report empty data or retry is a "
            "placeholder, not a reconstruction. "
            "repair_conformance contract has required_fixture_probe_operations, compiler "
            "output must declare an executable request_text probe for every named operation; "
            "these are newly reached task-plane frontier operations and cannot be replaced "
            "by a later repetition of an already-covered operation. When that field is empty, "
            "use the final late_observed_operation. A runtime "
            "handler without that declared probe is incomplete. Every declared probe is "
            "executed against its exact request branch before rollout, so its actual response "
            "must contain its own response_contains value. response_contains must remain a "
            "recorded scalar leaf used as the provenance assertion; the runtime response must "
            "carry the surrounding decoded container which contains that leaf. Do not replace "
            "response_contains with a serialized container, and do not return only the scalar "
            "from the runtime branch. Never remove or relocate the contract's exact_probe; it "
            "is an independent regression constraint and must retain its kind, path, and a "
            "fixture-derived scalar assertion even when a frontier probe is also required. "
            "Do not copy one fixture assertion "
            "onto every observed operation: operations must be implemented, but only the "
            "required frontier operation (or final late operation when no frontier is named) "
            "needs a fixture-derived assertion probe unless the contract names another exact "
            "probe. Remove only redundant non-exact probes whose protocol-shaped response cannot "
            "truthfully contain the assertion. Inspect the actual changed "
            "source before claiming this repair. "
            "Return the value of expected_output as exactly one JSON object, do not "
            "wrap it. Use at most one of content or patch_intent; both may be omitted "
            "when candidate-owned files themselves implement the reusable skill-package "
            "behavior delta.\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )
    return (
        "Generate one candidate package from this bounded EvolutionContext. "
        "Follow its population_strategy, required_behaviors, preserved_behaviors, "
        "capability_contracts, and acceptance_constraints. Prefer the smallest reusable "
        "behavior delta and keep domain implementations inside candidate-owned files. "
        "Use trace step summaries to infer reusable behavior, never to hard-code dataset "
        "task IDs, case IDs, original endpoints, or environment-specific paths. "
        "Replay harness files must accompany, not substitute for, a reusable task-execution "
        "improvement in the primary target content. Reconstruct fixture-derived task data "
        "for observed interactions; do not stop at control-plane handshakes, placeholder "
        "tokens, or empty protocol schemas. "
        "Evidence derivation entries may include a framework-generated response_index_path, "
        "response_record_count, and response_operations. Treat that sidecar as a bounded "
        "operation-to-record index: preserve it with the fixture, select the recorded "
        "payload for the incoming operation (or a deterministic cursor when the operation "
        "repeats), and keep the decoded response shape. Do not reduce the raw fixture to "
        "the first scalar while discarding its response records. "
        "During a frozen replay the same index is available to the skill runtime through "
        "AWORLD_REPLAY_RESPONSE_INDEX, with AWORLD_REPLAY_FIXTURE_PATH naming the immutable "
        "fixture. Read the sidecar at startup (or derive the adjacent .responses.json path), "
        "and use its payload_path/operation records to select a bounded response; never "
        "assume the top-level fixture is the task data. "
        "A helper such as _normalize_fixture_list that returns FIXTURE_DATA or "
        "FIXTURE_DATA.get(key, []) is not a reconstruction and will be rejected even "
        "when the returned outer list is non-empty; traverse the indexed gateway payload "
        "and project the nested recorded records instead. "
        "The minimal acceptable runtime pattern is: index_path = os.environ.get("
        "'AWORLD_REPLAY_RESPONSE_INDEX'); index = json.load(open(index_path)); "
        "records = [r for r in index['records'] if r['non_empty'] and "
        "r['operation'] == operation]; payload = resolve_payload_path(fixture, "
        "records[cursor[operation]]['payload_path']); then return a protocol-shaped "
        "projection of payload. Adapt the projection to the protocol, but do not "
        "replace this operation-to-record dependency with a global fixture list. "
        "A task-plane handler is non-conforming if an observed operation branch only "
        "returns RESPONSE_TOKEN/FIXTURE_CONTAINER (or an equivalent module-global) after "
        "reading params into an unused local. The required generic data flow is: derive "
        "operation = request[method/operation/command], read bounded request parameters, "
        "choose records = responses_by_operation[operation] (or advance a deterministic "
        "per-operation cursor), then project the selected recorded response shape. The "
        "operation/parameter value must reach the returned protocol result; an assignment "
        "such as params.get('expression') that is never used in the returned value does "
        "not satisfy this contract. "
        "When validation_feedback contains repair_candidate_package, repair that bounded "
        "candidate source directly and preserve its already-correct package behavior instead "
        "of rebuilding the capability from scratch. "
        "When repair_focus is present, use it as the base candidate source package for this "
        "population member: satisfy its observed failure and acceptance direction before "
        "addressing other historical feedback. When repair_support is present, it is one "
        "bounded complementary source package: transplant only the compatible branches "
        "that already satisfy a regression constraint, without replacing the focused base "
        "or reintroducing the support candidate's failed gate. Treat every other current validation "
        "diagnostic as a regression constraint on that repair: combine compatible fixes "
        "into the focused source, preserve behaviors that already advanced farther in the "
        "interaction, and do not mistake transport liveness for asynchronous completion. "
        "Treat repair_conformance as a machine-enforced pre-rollout gate: materially change "
        "one required_branch_path from the focused package; satisfy exact_probe when present; "
        "and when requires_fixture_derived_probe is true, declare a probe for each "
        "required_fixture_probe_operation (or the final late_observed_operation when the "
        "required list is empty) whose non-empty response_contains comes from the recorded "
        "fixture and whose runtime branch returns that content in a protocol-valid response. "
        "Every declared probe will be executed before rollout. Do not mechanically declare one "
        "probe per observed operation or reuse an assertion on a branch that does not return it: "
        "implement all observed operations, but keep only the required frontier fixture-derived "
        "probe plus independently valid discovery/exact probes. "
        "For late_fixture_probe_not_recorded, late_fixture_probe_outside_recorded_payload, "
        "or exact_repair_probe_not_recorded, replace "
        "raw-byte token regexes and mapping-key extraction rather than adding more of them: "
        "parse JSON or JSONL, recursively decode bounded string leaves that themselves contain "
        "JSON objects or arrays, and recursively traverse mapping values and list items (never "
        "mapping keys). Search arbitrary nesting with a bounded node count rather than a shallow "
        "depth cutoff. When the fixture is a trajectory envelope, first complete a gateway-"
        "discovery pass for action_result or tool_outputs at any depth before selecting any "
        "scalar; if at least one gateway exists, never fall back to request/action branches. "
        "Implement this as two genuinely separate phases: phase 1 walks every composite node "
        "and only appends values whose key is action_result or tool_outputs to a gateways list; "
        "phase 1 must never inspect, collect, or return a scalar. Only after phase 1 completes may "
        "phase 2 select payload values from gateways. Do not share a found-scalars list between "
        "gateway discovery and traversal of the outer trajectory. "
            "The required structure is gateways = collect_gateways(root); if gateways: for gateway "
            "in gateways: payloads = collect_payloads(gateway); select_leaf(payloads). Never call "
            "select_leaf(gateway), and never make collect_payloads(parsed_root) the phase-2 path when "
            "a gateway exists. Phase 2 means payload selection inside gateways, not the absence of a "
            "gateway. Only if gateways is empty may a generic non-trajectory fallback inspect root. "
            "When selecting scalar leaves, reject bool explicitly before accepting int or float because "
            "Python bool is an int subclass; append the gateway subtree (for example root[key]), never "
            "the surrounding root/container itself. "
            "Keep the gateway-key set limited to action_result and tool_outputs; content, response, "
        "result, output, body, and data are a separate payload-key set that is valid only after "
        "entering a gateway. "
        "Inside action_result, ignore metadata such as tool name, call "
        "id, success, and timing fields until reaching a content, response, result, output, body, "
        "or data payload. If a gateway value is a list, run this payload-key selection on each "
        "list item; never pass the whole list to a generic scalar traversal that can see metadata. "
        "tool_outputs itself is an output payload. Only then recursively decode "
        "nested containers. Do not globally interpret same-named keys from request/action records "
        "as responses. Select a deterministic non-empty scalar leaf without requiring an "
        "alphanumeric-only shape or an arbitrary narrow length range. Never substitute a fixture "
        "hash or placeholder when such a token regex finds nothing. "
        "Reuse one canonical selector with identical behavior in compiler and runtime. "
        "The same selected leaf may be reused by multiple probes; do not add key extraction "
        "or raw-token fallbacks merely to manufacture distinct per-probe tokens. "
        "Use that scalar only as the probe assertion: the task-plane handler must return the "
        "surrounding non-empty decoded recorded container (or a protocol-shaped projection "
        "that preserves multiple recorded response values), not collapse the response to the "
        "single assertion token. Task-plane handlers must not return one global fixture "
        "container for every observed operation: correlate each request operation and its "
        "bounded parameters with the corresponding recorded response context (or a deterministic "
        "per-operation cursor), preserving the operation's response shape. A repeated generic "
        "token/container that causes the caller to report empty data or retry is a placeholder "
        "and is non-conforming even when a probe substring matches. Choose request_text probe "
        "inputs that actually execute this "
        "fixture-derived branch; do not use an input that the handler maps to a constant result. "
        "Treat any previous expected_preview as diagnostic evidence rather than a value to "
        "hard-code. Place the selected leaf inside "
        "the correlated protocol result payload, not in unrelated envelope metadata. "
        "For a bounded change to a large current target, use patch_intent so all unrelated "
        "content remains byte-for-byte preserved; do not reconstruct a shortened full copy. "
        "Return the value of expected_output as exactly one JSON object; do not wrap it. "
        "Use at most one of content or patch_intent; both may be omitted only when "
        "candidate-owned files themselves implement the reusable skill-package behavior "
        "delta rather than a fixture-only placeholder.\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def _overlay_repair_focus_files(
    request: OptimizerRequest,
    *,
    candidate_index: int,
    candidate_files: tuple[CandidateFileDelta, ...],
) -> tuple[tuple[CandidateFileDelta, ...], int]:
    """Apply a repair response as a delta over its focused candidate package."""

    context = request.evolution_context or compile_evolution_context(request)
    payload = context.to_prompt_payload(candidate_index=candidate_index)
    repair_focus = payload.get("repair_focus")
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
) -> tuple[str, str, str, tuple[CandidateFileDelta, ...]]:
    if isinstance(output, Mapping):
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
        content = apply_skill_patch_intent(request.current_content, patch_intent)
        materialization = "patch_intent"
    else:
        materialization = "full_content"
    if not isinstance(content, str) or not content:
        if isinstance(raw_files, (list, tuple)) and any(
            isinstance(item, Mapping) for item in raw_files
        ):
            content = request.current_content
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
