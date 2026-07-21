from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from aworld.self_evolve.datasets import EvalCase
from aworld.self_evolve.evolution_context import (
    EVOLUTION_CONTEXT_SCHEMA_VERSION,
    MAX_CONTEXT_TRACE_CHARS,
    compile_evolution_context,
)
from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureEvent,
    ReplayFailureObservation,
    aggregate_replay_failure_observations,
)
from aworld.self_evolve.lessons import LessonRecord, extract_lesson_records
from aworld.self_evolve.optimizers.base import OptimizerRequest
from aworld.self_evolve.replay_adaptation import ReplayCapabilityRequirement
from aworld.self_evolve.sanitization import sanitize_source_text
from aworld.self_evolve.trace_pack import TraceEvidenceStep, TracePack
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


def test_lesson_context_ranks_unique_repairable_cause_without_occurrence_bias() -> None:
    low_value = tuple(
        LessonRecord(
            lesson_id=f"success-{index:02d}",
            lesson_type="success_memory",
            title="Preserve success",
            summary=f"success memory {index}",
            occurrence_count=1000,
        )
        for index in range(40)
    )
    causal = LessonRecord(
        lesson_id="causal-priority",
        lesson_type="causal_failure_memory",
        title="Repair typed cause",
        summary="Repair a typed capability cause.",
        metrics={"repairable": True, "causal_code": "contract_rejected"},
        occurrence_count=1,
        distinct_source_count=2,
        source_task_ids=("task-a", "task-b"),
        affected_case_ids=("case-a", "case-b"),
    )
    request = replace(_request(), lesson_records=(*low_value, causal, causal))

    payload = compile_evolution_context(request).to_prompt_payload(candidate_index=0)
    lesson_payloads = payload["lesson_records"]
    assert len(lesson_payloads) == 32
    assert lesson_payloads[0]["lesson_id"] == "causal-priority"
    assert sum(item["lesson_id"] == "causal-priority" for item in lesson_payloads) == 1
    assert lesson_payloads[0]["occurrence_count"] == 1
    assert lesson_payloads[0]["distinct_source_count"] == 2


def test_private_v2_causal_aggregate_reaches_prompt_as_identity_digests_only() -> None:
    private_capability = "PRIVATE_CAPABILITY_IDENTITY"
    private_requirement = "PRIVATE_REQUIREMENT_IDENTITY"
    private_contract = "PRIVATE_CONTRACT_FINGERPRINT"
    aggregate = aggregate_replay_failure_observations(
        tuple(
            ReplayFailureObservation(
                event=ReplayFailureEvent(
                    event_id=f"private-causal-event-{index}",
                    code="capability_contract_rejected",
                    owner=FailureOwner.CANDIDATE,
                    stage=FailureStage.CAPABILITY_PREFLIGHT,
                    scope=FailureScope.CANDIDATE,
                    repairable=True,
                    capability_id=private_capability,
                    requirement_id=private_requirement,
                    contract_fingerprint=private_contract,
                ),
                case_id=f"private-case-{index}",
                run_id=f"private-run-{index}",
                task_id=f"private-task-{index}",
                candidate_id=f"private-candidate-{index}",
            )
            for index in range(3)
        )
    )[0]
    lessons = extract_lesson_records(
        (
            EvaluationSummary(
                variant_id="private-candidate",
                dataset_split="validation",
                metrics={"causal_failure_events": [aggregate.to_dict()]},
            ),
        ),
        target_scope={"target_type": "skill", "target_id": "demo"},
    )

    assert len(lessons) == 1
    assert lessons[0].occurrence_count == 3
    assert lessons[0].affected_case_count == 3
    assert lessons[0].distinct_source_count == 3
    causal_metrics = lessons[0].metrics
    assert "capability_id" not in causal_metrics
    assert "requirement_id" not in causal_metrics
    assert "contract_fingerprint" not in causal_metrics
    assert causal_metrics["capability_identity_digest"] == (
        aggregate.capability_identity_digest
    )
    assert causal_metrics["requirement_identity_digest"] == (
        aggregate.requirement_identity_digest
    )
    assert causal_metrics["contract_identity_digest"] == (
        aggregate.contract_identity_digest
    )

    payload = compile_evolution_context(
        replace(_request(), lesson_records=lessons)
    ).to_prompt_payload(candidate_index=0)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert private_capability not in encoded
    assert private_requirement not in encoded
    assert private_contract not in encoded
    assert aggregate.capability_identity_digest in encoded
    assert aggregate.requirement_identity_digest in encoded
    assert aggregate.contract_identity_digest in encoded


def test_legacy_lesson_metrics_use_recursive_public_projection_in_prompt() -> None:
    private_capability = "LEGACY_PRIVATE_CAPABILITY"
    private_requirement = "LEGACY_PRIVATE_REQUIREMENT"
    private_contract = "LEGACY_PRIVATE_CONTRACT"
    private_response = "LEGACY_PRIVATE_EXPECTED_RESPONSE"
    legacy_lesson = LessonRecord(
        lesson_id="legacy-private-lesson",
        lesson_type="causal_failure_memory",
        title="Legacy causal lesson",
        summary="Legacy external lesson with nested diagnostics.",
        metrics={
            "repairable": True,
            "capability_id": private_capability,
            "requirement_id": private_requirement,
            "contract_fingerprint": private_contract,
            "nested": [
                {
                    "repair_conformance": {
                        "focus_candidate_id": "candidate-legacy",
                        "contract_fingerprint": private_contract,
                        "exact_probe": {
                            "method": "POST",
                            "expected_response": private_response,
                        },
                    }
                }
            ],
        },
    )

    payload = compile_evolution_context(
        replace(_request(), lesson_records=(legacy_lesson,))
    ).to_prompt_payload(candidate_index=0)
    lesson_metrics = payload["lesson_records"][0]["metrics"]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    encoded_metrics = json.dumps(lesson_metrics, ensure_ascii=False, sort_keys=True)

    assert private_capability not in encoded
    assert private_requirement not in encoded
    assert private_contract not in encoded
    assert private_response not in encoded
    assert '"capability_id":' not in encoded_metrics
    assert '"requirement_id":' not in encoded_metrics
    assert '"contract_fingerprint":' not in encoded_metrics
    assert lesson_metrics["capability_identity_digest"] == hashlib.sha256(
        private_capability.encode("utf-8")
    ).hexdigest()
    assert lesson_metrics["requirement_identity_digest"] == hashlib.sha256(
        private_requirement.encode("utf-8")
    ).hexdigest()
    assert lesson_metrics["contract_identity_digest"] == hashlib.sha256(
        private_contract.encode("utf-8")
    ).hexdigest()
    public_probe = lesson_metrics["nested"][0]["repair_conformance"]["exact_probe"]
    assert "expected_response" not in public_probe
    assert public_probe["expected_response_fingerprint"].startswith("sha256:")
    assert public_probe["expected_response_shape"] == {
        "kind": "text",
        "bytes": len(private_response.encode("utf-8")),
    }


def test_compiler_deduplicates_feedback_and_selects_typed_strategies() -> None:
    context = compile_evolution_context(_request())

    assert context.schema_version == EVOLUTION_CONTEXT_SCHEMA_VERSION
    assert len(context.validation_feedback) == 1
    assert context.population_strategies == (
        "quality_regression_repair",
        "missing_capability_completion",
        "minimal_behavior_delta",
        "efficiency_and_robustness",
    )
    assert [item["capability_type"] for item in context.capability_contracts] == [
        "replay"
    ]
    assert "reconstruct_fixture_derived_task_data_plane" in (
        context.acceptance_constraints
    )


def test_compiler_does_not_treat_prior_feedback_as_current_iteration_repair() -> None:
    request = _request()
    context = compile_evolution_context(
        replace(
            request,
            validation_feedback=(),
        )
    )

    assert context.population_strategies[:2] == (
        "minimal_behavior_delta",
        "missing_capability_completion",
    )


def test_compiler_preserves_bounded_repair_candidate_package_source() -> None:
    runtime_source = (
        "def handle_websocket_frame(frame):\n"
        "    token = match.group().decode(\"ascii\")\n"
        "    # preserve enough source for the next candidate to repair\n"
        + "    frame = frame\n" * 40
        + "    return b'incomplete-control-frame'\n"
    )
    feedback = EvaluationSummary(
        variant_id="candidate-runtime",
        dataset_split="validation",
        metrics={
            "failed_gates": ["candidate_replay"],
            "candidate_validation_diagnostics": [
                {
                    "code": "failed_gate",
                    "stage": "candidate_replay",
                    "reason": "WebSocket control frame failed",
                }
            ],
            "repair_candidate_package": {
                "candidate_id": "candidate-runtime",
                "rationale": "candidate-owned runtime",
                "files": [
                    {
                        "path": "replay/runtime.py",
                        "operation": "upsert",
                        "executable": False,
                        "content": runtime_source,
                    }
                ],
            },
        },
    )

    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(feedback,),
            prior_feedback=(),
        )
    )

    compiled_feedback = context.validation_feedback[0]
    assert compiled_feedback["candidate_validation_diagnostics"][0]["reason"] == (
        "WebSocket control frame failed"
    )
    package = compiled_feedback["repair_candidate_package"]
    assert package["candidate_id"] == "candidate-runtime"
    assert package["files"][0]["path"] == "replay/runtime.py"
    assert package["files"][0]["content"] == runtime_source.strip()
    assert len(package["files"][0]["content"]) > 240


def test_compiler_preserves_complete_large_repair_runtime_source() -> None:
    runtime_source = (
        "def handle(message):\n"
        + "    observed = message\n" * 800
        + "def main():\n    return 'runtime-tail-preserved'\n"
    )
    assert len(runtime_source) > 16_000
    feedback = EvaluationSummary(
        variant_id="candidate-large-runtime",
        dataset_split="validation",
        metrics={
            "failed_gates": ["candidate_replay"],
            "repair_candidate_package": {
                "candidate_id": "candidate-large-runtime",
                "rationale": "preserve complete source",
                "files": [
                    {
                        "path": "replay/runtime.py",
                        "operation": "upsert",
                        "executable": False,
                        "content": runtime_source,
                    }
                ],
            },
        },
    )

    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(feedback,),
            prior_feedback=(),
        )
    )

    preserved = context.validation_feedback[0]["repair_candidate_package"][
        "files"
    ][0]["content"]
    assert preserved == runtime_source.strip()
    assert preserved.endswith("return 'runtime-tail-preserved'")


def test_source_sanitizer_preserves_expressions_and_redacts_literals() -> None:
    source = (
        'token = match.group().decode("ascii")\n'
        'api_key = "sk-private-literal-value"\n'
    )

    sanitized = sanitize_source_text(source)

    assert 'token = match.group().decode("ascii")' in sanitized
    assert 'api_key = "<REDACTED_SECRET>"' in sanitized
    assert "sk-private-literal-value" not in sanitized


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


def test_prompt_payload_assigns_one_recent_repair_focus_per_population_member() -> None:
    def repair_feedback(candidate_id: str, reason: str) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate_id,
            dataset_split="validation",
            metrics={
                "failed_gates": ["candidate_replay"],
                "candidate_validation_diagnostics": [
                    {
                        "code": "failed_gate",
                        "stage": "candidate_replay",
                        "reason": reason,
                    }
                ],
                "repair_candidate_package": {
                    "candidate_id": candidate_id,
                    "files": [
                        {
                            "path": "replay/runtime.py",
                            "content": f"# {candidate_id}\n",
                        }
                    ],
                },
            },
        )

    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(
                repair_feedback("candidate-probe", "declared probe mismatch"),
                repair_feedback("candidate-task", "real task rejected endpoint"),
            ),
            prior_feedback=(),
        )
    )

    first_payload = context.to_prompt_payload(candidate_index=0)
    second_payload = context.to_prompt_payload(candidate_index=1)

    assert first_payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-task"
    assert second_payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-probe"
    assert first_payload["repair_support"]["repair_candidate_id"] == (
        "candidate-probe"
    )
    assert second_payload["repair_support"]["repair_candidate_id"] == (
        "candidate-task"
    )
    for payload in (first_payload, second_payload):
        assert payload["repair_support"]["repair_candidate_source_omitted"] is True
        assert "repair_candidate_package" not in payload["repair_support"]
        assert all(
            "repair_candidate_package" not in item
            for item in payload["validation_feedback"]
        )
        assert payload["validation_feedback"][0] != payload["repair_focus"]


def test_focused_repair_prompt_budgets_source_without_affecting_overlay_base() -> None:
    def repair_feedback(candidate_id: str) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate_id,
            dataset_split="validation",
            metrics={
                "failed_gates": ["candidate_replay"],
                "interaction_progress": 10,
                "candidate_validation_diagnostics": [
                    {
                        "code": "implement_observed_endpoint_interactions",
                        "stage": "replay_capability",
                        "observed_request_operations": ["records.query"],
                    }
                ],
                "repair_candidate_package": {
                    "candidate_id": candidate_id,
                    "files": [
                        {
                            "path": "SKILL.md",
                            "content": "# Skill\n" + ("s" * 15_990),
                        },
                        {
                            "path": "replay/compiler.py",
                            "content": "# compiler\n" + ("c" * 15_988),
                        },
                        {
                            "path": "replay/runtime.py",
                            "content": "# runtime\n" + ("r" * 31_990),
                        },
                    ],
                },
            },
        )

    context = compile_evolution_context(
        replace(
            _request(),
            current_content="# Baseline\n" + ("b" * 20_000),
            validation_feedback=(
                repair_feedback("candidate-support"),
                repair_feedback("candidate-focus"),
            ),
            prior_feedback=(),
        )
    )

    payload = context.to_prompt_payload(candidate_index=0)
    prompt_package = payload["repair_focus"]["repair_candidate_package"]
    prompt_files = {
        item["path"]: item
        for item in prompt_package["files"]
    }

    assert payload["current_content"] == ""
    assert payload["target_package_inventory"] == []
    assert payload["capability_requirements"] == []
    assert payload["capability_contracts"] == []
    assert prompt_files["replay/runtime.py"]["content"].startswith("# runtime")
    assert prompt_files["replay/compiler.py"]["content_omitted"] is True
    assert "content" not in prompt_files["replay/compiler.py"]
    assert prompt_files["SKILL.md"]["content_omitted"] is True
    assert "content" not in prompt_files["SKILL.md"]
    assert payload["repair_prompt_budget"] == {
        "source_chars": 40_000,
        "omitted_current_content_chars": 20_011,
        "omitted_target_package_inventory_items": 1,
        "omitted_capability_requirements": 1,
        "omitted_capability_contracts": 1,
    }
    assert "repair_candidate_package" not in payload["repair_support"]
    assert len(json.dumps(payload, ensure_ascii=False, sort_keys=True)) < 62_000

    overlay_focus = context.repair_focus_for_candidate(candidate_index=0)
    assert overlay_focus is not None
    overlay_files = {
        item["path"]: item
        for item in overlay_focus["repair_candidate_package"]["files"]
    }
    assert overlay_files["SKILL.md"]["content"].startswith("# Skill")


def test_prompt_payload_budgets_accumulated_historical_feedback() -> None:
    feedback = tuple(
        EvaluationSummary(
            variant_id=f"candidate-{index}",
            dataset_split="historical",
            metrics={
                "failed_gates": [f"gate-{index}"],
                "required_behaviors": [
                    f"behavior-{index}-{item}-" + ("x" * 180)
                    for item in range(8)
                ],
                "candidate_validation_diagnostics": [
                    {
                        "code": f"diagnostic-{index}-{item}",
                        "stage": "candidate_replay",
                        "reason": "r" * 1_000,
                    }
                    for item in range(8)
                ],
            },
        )
        for index in range(24)
    )
    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(),
            prior_feedback=feedback,
        )
    )

    payload = context.to_prompt_payload(candidate_index=0)

    serialized_feedback = json.dumps(
        payload["validation_feedback"],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert len(serialized_feedback) <= 24_000
    assert payload["validation_feedback"][0]["variant_id"] == "candidate-0"
    assert len(payload["required_behaviors"]) <= 64
    assert len(json.dumps(payload, ensure_ascii=False, sort_keys=True)) < 65_000


def test_prompt_payload_compiles_machine_checked_repair_as_focused_context() -> None:
    runtime_source = "def handle(value):\n    return value\n" + ("# source\n" * 200)
    feedback = EvaluationSummary(
        variant_id="candidate-focused",
        dataset_split="validation",
        metrics={
            "failed_gates": ["candidate_repair_conformance"],
            "candidate_validation_diagnostics": [
                {
                    "code": "failed_gate",
                    "details": {
                        "stage": "repair_conformance",
                        "code": "late_fixture_probe_not_recorded",
                        "required_reconstruction_algorithm": [
                            "decode the recorded response container"
                        ],
                    },
                }
            ],
            "repair_candidate_package": {
                "candidate_id": "candidate-focused",
                "files": [
                    {
                        "path": "replay/runtime.py",
                        "operation": "upsert",
                        "content": runtime_source,
                    }
                ],
            },
        },
    )
    context = compile_evolution_context(
        replace(
            _request(),
            trace_packs=(
                TracePack(
                    pack_id="trajectory:focused",
                    source_kind="trajectory_log",
                    task_id="focused",
                    steps=(
                        TraceEvidenceStep(
                            evidence_id="focused:1",
                            source_index=0,
                            original_id="1",
                            state={"messages": [{"content": "x" * 8_000}]},
                            action={"content": "repair"},
                            reward=None,
                            agent_id="main",
                            tool_names=(),
                        ),
                    ),
                ),
            ),
            validation_feedback=(feedback,),
            prior_feedback=(),
        )
    )

    payload = context.to_prompt_payload(candidate_index=0)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    assert payload["repair_context_mode"] == "focused_candidate_delta"
    assert payload["trainable_cases"] == []
    assert payload["trace_evidence"] == []
    assert payload["lesson_records"] == []
    assert payload["repair_focus"]["repair_candidate_package"]["files"][0][
        "content"
    ] == runtime_source.strip()
    assert serialized.count("def handle(value)") == 1


def test_prompt_payload_omits_repair_focus_without_candidate_source() -> None:
    context = compile_evolution_context(_request())

    payload = context.to_prompt_payload(candidate_index=0)

    assert "repair_focus" not in payload


def test_prompt_payload_omits_support_that_failed_same_specific_repair_gate() -> None:
    def repair_feedback(candidate_id: str, code: str) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate_id,
            dataset_split="validation",
            metrics={
                "failed_gates": ["candidate_repair_conformance"],
                "candidate_validation_diagnostics": [
                    {
                        "code": "failed_gate",
                        "details": {
                            "stage": "repair_conformance",
                            "code": code,
                        },
                    }
                ],
                "repair_candidate_package": {
                    "candidate_id": candidate_id,
                    "files": [
                        {
                            "path": "replay/runtime.py",
                            "content": f"# {candidate_id}\n",
                        }
                    ],
                },
            },
        )

    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(
                repair_feedback(
                    "candidate-one",
                    "late_fixture_probe_not_recorded",
                ),
                repair_feedback(
                    "candidate-two",
                    "late_fixture_probe_outside_recorded_payload",
                ),
            ),
            prior_feedback=(),
        )
    )

    payload = context.to_prompt_payload(candidate_index=0)

    assert "repair_support" not in payload


def test_prompt_payload_prioritizes_real_task_protocol_failure_over_newer_probe() -> None:
    def feedback(candidate_id: str, code: str) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate_id,
            dataset_split="validation",
            metrics={
                "failed_gates": ["candidate_replay"],
                "candidate_validation_diagnostics": [
                    {
                        "code": code,
                        "stage": "replay_capability",
                        "reason": code,
                    }
                ],
                "repair_candidate_package": {
                    "candidate_id": candidate_id,
                    "files": [
                        {"path": "replay/runtime.py", "content": f"# {candidate_id}"}
                    ],
                },
            },
        )

    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(
                feedback("candidate-task", "implement_observed_endpoint_interactions"),
                feedback("candidate-probe", "verify_declared_protocol_probe_branch"),
            ),
            prior_feedback=(),
        )
    )

    payload = context.to_prompt_payload(candidate_index=0)

    assert payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-task"


def test_prompt_payload_prioritizes_authoritative_repair_over_screening_progress() -> None:
    def feedback(
        candidate_id: str,
        code: str,
        *,
        authoritative: bool = False,
    ) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate_id,
            dataset_split="historical_repair",
            metrics={
                "failed_gates": ["replay_adaptation" if authoritative else "candidate_replay"],
                "authoritative_replay_failure": authoritative,
                "candidate_validation_diagnostics": [
                    {
                        "code": code,
                        "stage": "capability_compile" if authoritative else "task_rollout",
                        "reason": code,
                    }
                ],
                "repair_candidate_package": {
                    "candidate_id": candidate_id,
                    "files": [
                        {"path": "replay/compiler.py", "content": f"# {candidate_id}"}
                    ],
                },
            },
        )

    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(
                feedback(
                    "candidate-authoritative",
                    "invalid_replay_capability_compile",
                    authoritative=True,
                ),
                feedback(
                    "candidate-screening",
                    "implement_observed_endpoint_interactions",
                ),
            ),
            prior_feedback=(),
        )
    )

    payload = context.to_prompt_payload(candidate_index=0)

    assert payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-authoritative"


def test_prompt_payload_prioritizes_judged_held_out_repair_over_replay_history() -> None:
    judged = EvaluationSummary(
        variant_id="candidate-judged",
        dataset_split="held_out",
        metrics={
            "score": 69.6,
            "A1_groundedness": 3,
            "A2_completeness": 4,
            "evidence_incomplete": True,
            "failed_gates": ["evidence_quality", "held_out_verification"],
            "repair_candidate_package": {
                "candidate_id": "candidate-judged",
                "files": [
                    {"path": "SKILL.md", "content": "# judged task output\n"}
                ],
            },
        },
    )
    stale_replay = EvaluationSummary(
        variant_id="candidate-stale-replay",
        dataset_split="validation",
        metrics={
            "failed_gates": ["candidate_replay"],
            "authoritative_replay_failure": True,
            "interaction_progress": 999,
            "candidate_validation_diagnostics": [
                {
                    "code": "missing_fixture_data",
                    "stage": "task_rollout",
                }
            ],
            "repair_candidate_package": {
                "candidate_id": "candidate-stale-replay",
                "files": [
                    {"path": "replay/runtime.py", "content": "# stale replay\n"}
                ],
            },
        },
    )

    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(judged, stale_replay),
            prior_feedback=(),
        )
    )

    first_payload = context.to_prompt_payload(candidate_index=0)
    second_payload = context.to_prompt_payload(candidate_index=1)

    assert first_payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-judged"
    assert second_payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-judged"
    assert first_payload["validation_feedback"][0]["dataset_split"] == "held_out"
    assert "repair_support" not in first_payload
    assert "repair_support" not in second_payload
    assert "repair_conformance" not in first_payload
    assert "repair_conformance" not in second_payload


def test_prompt_payload_prefers_current_run_frontier_over_historical_authoritative_repair() -> None:
    def feedback(
        candidate_id: str,
        *,
        authoritative: bool = False,
    ) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate_id,
            dataset_split="historical_repair" if authoritative else "validation",
            metrics={
                "failed_gates": ["replay_adaptation" if authoritative else "candidate_replay"],
                "authoritative_replay_failure": authoritative,
                "candidate_validation_diagnostics": [
                    {
                        "code": (
                            "invalid_replay_capability_compile"
                            if authoritative
                            else "implement_async_endpoint_completion"
                        ),
                        "stage": "capability_compile" if authoritative else "task_rollout",
                    }
                ],
                "repair_candidate_package": {
                    "candidate_id": candidate_id,
                    "files": [
                        {"path": "replay/runtime.py", "content": f"# {candidate_id}"}
                    ],
                },
            },
        )

    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(feedback("candidate-current"),),
            prior_feedback=(feedback("candidate-historical", authoritative=True),),
        )
    )

    payload = context.to_prompt_payload(candidate_index=0)

    assert payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-current"
    historical = next(
        item
        for item in payload["validation_feedback"]
        if item["variant_id"] == "candidate-historical"
    )
    assert "repair_candidate_package" not in historical


def test_prompt_payload_keeps_all_population_members_on_task_plane_frontier() -> None:
    def feedback(candidate_id: str, code: str) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate_id,
            dataset_split="validation",
            metrics={
                "failed_gates": ["candidate_replay"],
                "candidate_validation_diagnostics": [
                    {
                        "code": code,
                        "stage": "replay_capability",
                        "reason": code,
                    }
                ],
                "repair_candidate_package": {
                    "candidate_id": candidate_id,
                    "files": [
                        {"path": "replay/runtime.py", "content": f"# {candidate_id}"}
                    ],
                },
            },
        )

    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(
                feedback("candidate-task-old", "implement_observed_endpoint_interactions"),
                feedback("candidate-task-new", "implement_observed_endpoint_interactions"),
                feedback("candidate-probe-newest", "verify_declared_protocol_probe_branch"),
            ),
            prior_feedback=(),
        )
    )

    first_payload = context.to_prompt_payload(candidate_index=0)
    second_payload = context.to_prompt_payload(candidate_index=1)

    assert first_payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-task-new"
    assert second_payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-task-new"
    assert first_payload["repair_conformance"][
        "requires_fixture_derived_probe"
    ] is True
    assert second_payload["repair_conformance"] == first_payload[
        "repair_conformance"
    ]


def test_prompt_payload_prioritizes_async_completion_frontier_over_probe_repair() -> None:
    def feedback(candidate_id: str, code: str) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate_id,
            dataset_split="validation",
            metrics={
                "failed_gates": ["candidate_replay"],
                "candidate_validation_diagnostics": [
                    {
                        "code": code,
                        "stage": "replay_capability",
                        "reason": code,
                    }
                ],
                "repair_candidate_package": {
                    "candidate_id": candidate_id,
                    "files": [
                        {"path": "replay/runtime.py", "content": f"# {candidate_id}"}
                    ],
                },
            },
        )

    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(
                feedback(
                    "candidate-async",
                    "implement_async_endpoint_completion",
                ),
                feedback(
                    "candidate-probe-newer",
                    "verify_declared_protocol_probe_branch",
                ),
            ),
            prior_feedback=(),
        )
    )

    payload = context.to_prompt_payload(candidate_index=0)

    assert payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-async"


def test_prompt_payload_prioritizes_routing_continuity_over_generic_async_repair() -> None:
    def feedback(candidate_id: str, code: str) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate_id,
            dataset_split="validation",
            metrics={
                "failed_gates": ["candidate_replay"],
                "candidate_validation_diagnostics": [
                    {
                        "code": code,
                        "stage": "replay_capability",
                        "reason": code,
                    }
                ],
                "repair_candidate_package": {
                    "candidate_id": candidate_id,
                    "files": [
                        {"path": "replay/runtime.py", "content": f"# {candidate_id}"}
                    ],
                },
            },
        )

    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(
                feedback(
                    "candidate-async",
                    "implement_async_endpoint_completion",
                ),
                feedback(
                    "candidate-routing",
                    "preserve_protocol_routing_continuity",
                ),
            ),
            prior_feedback=(),
        )
    )

    payload = context.to_prompt_payload(candidate_index=0)

    assert payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-routing"


def test_prompt_payload_prefers_deeper_interaction_within_same_failure_family() -> None:
    def feedback(candidate_id: str, progress: int) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate_id,
            dataset_split="validation",
            metrics={
                "failed_gates": ["candidate_replay"],
                "interaction_progress": progress,
                "candidate_validation_diagnostics": [
                    {
                        "code": "implement_async_endpoint_completion",
                        "stage": "replay_capability",
                        "reason": "navigation awaits completion",
                    }
                ],
                "repair_candidate_package": {
                    "candidate_id": candidate_id,
                    "files": [
                        {"path": "replay/runtime.py", "content": f"# {candidate_id}"}
                    ],
                },
            },
        )

    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(
                feedback("candidate-deeper", 32),
                feedback("candidate-newer-shallow", 6),
            ),
            prior_feedback=(),
        )
    )

    payload = context.to_prompt_payload(candidate_index=0)

    assert payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-deeper"


def test_prompt_payload_prefers_task_plane_timeout_over_transport_abort() -> None:
    def feedback(
        candidate_id: str,
        code: str,
        progress: int,
    ) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate_id,
            dataset_split="validation",
            metrics={
                "failed_gates": ["candidate_replay"],
                "interaction_progress": progress,
                "candidate_validation_diagnostics": [
                    {
                        "code": code,
                        "stage": "replay_capability",
                        "reason": "bounded candidate failure",
                        "observed_request_operations": (
                            ["session.open", "records.query"]
                            if code == "implement_observed_endpoint_interactions"
                            else []
                        ),
                    }
                ],
                "repair_candidate_package": {
                    "candidate_id": candidate_id,
                    "files": [
                        {"path": "replay/runtime.py", "content": f"# {candidate_id}"}
                    ],
                },
            },
        )

    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(
                feedback(
                    "candidate-task-plane",
                    "implement_observed_endpoint_interactions",
                    138,
                ),
                feedback(
                    "candidate-transport-abort",
                    "diagnose_protocol_handler_abort",
                    8,
                ),
            ),
            prior_feedback=(),
        )
    )

    payload = context.to_prompt_payload(candidate_index=0)

    assert payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-task-plane"
    assert payload["repair_conformance"]["focus_candidate_id"] == (
        "candidate-task-plane"
    )
    assert payload["repair_conformance"]["late_observed_operations"] == [
        "session.open",
        "records.query",
    ]
    assert payload["repair_conformance"]["requires_fixture_derived_probe"] is True


def test_prompt_payload_does_not_regress_task_plane_frontier_to_transport_conformance() -> None:
    task_plane = EvaluationSummary(
        variant_id="candidate-task-plane",
        dataset_split="validation",
        metrics={
            "failed_gates": ["candidate_replay"],
            "interaction_progress": 138,
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "stage": "replay_capability",
                    "observed_request_operations": ["records.query"],
                }
            ],
            "repair_candidate_package": {
                "candidate_id": "candidate-task-plane",
                "files": [
                    {"path": "replay/runtime.py", "content": "# task plane\n"}
                ],
            },
        },
    )
    conformance = EvaluationSummary(
        variant_id="candidate-conformance",
        dataset_split="validation",
        metrics={
            "failed_gates": ["candidate_replay"],
            "interaction_progress": 8,
            "candidate_validation_diagnostics": [
                {
                    "code": "diagnose_protocol_handler_abort",
                    "stage": "replay_capability",
                    "observed_fixture_root_types": ["array"],
                },
                {
                    "code": "failed_gate",
                    "stage": "candidate_replay",
                    "details": {
                        "stage": "repair_conformance",
                        "code": "repair_probe_execution_failed",
                    },
                },
            ],
            "repair_candidate_package": {
                "candidate_id": "candidate-conformance",
                "files": [
                    {"path": "replay/runtime.py", "content": "# conformance\n"}
                ],
            },
        },
    )
    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(task_plane, conformance),
            prior_feedback=(),
        )
    )

    payload = context.to_prompt_payload(candidate_index=0)

    assert payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-task-plane"
    assert payload["repair_support"]["repair_candidate_id"] == (
        "candidate-conformance"
    )
    assert payload["repair_support"]["repair_candidate_source_omitted"] is True


def test_prompt_payload_prioritizes_conformance_that_inherits_task_plane_frontier() -> None:
    def feedback(
        candidate_id: str,
        *,
        code: str,
        inherited_task_plane: bool,
    ) -> EvaluationSummary:
        contract = {
            "focus_candidate_id": "candidate-parent",
            "failure_codes": (
                ["implement_observed_endpoint_interactions"]
                if inherited_task_plane
                else ["diagnose_protocol_handler_abort"]
            ),
            "interaction_progress": 138 if inherited_task_plane else 8,
            "base_file_fingerprints": {
                "replay/runtime.py": "sha256:old"
            },
            "required_branch_paths": ["replay/runtime.py"],
            "base_branch_fingerprints": {},
            "late_observed_operations": (
                ["records.query"] if inherited_task_plane else []
            ),
            "requires_fixture_derived_probe": inherited_task_plane,
        }
        return EvaluationSummary(
            variant_id=candidate_id,
            dataset_split="validation",
            metrics={
                "failed_gates": ["candidate_replay"],
                "interaction_progress": contract["interaction_progress"],
                "candidate_validation_diagnostics": [
                    {
                        "code": "failed_gate",
                        "stage": "candidate_replay",
                        "details": {
                            "stage": "repair_conformance",
                            "code": code,
                            "repair_conformance": contract,
                        },
                    }
                ],
                "repair_candidate_package": {
                    "candidate_id": candidate_id,
                    "files": [
                        {
                            "path": "replay/runtime.py",
                            "content": f"# {candidate_id}",
                        }
                    ],
                },
            },
        )

    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(
                feedback(
                    "candidate-data-frontier-conformance",
                    code="late_fixture_probe_missing",
                    inherited_task_plane=True,
                ),
                feedback(
                    "candidate-transport-conformance-newer",
                    code="repair_probe_execution_failed",
                    inherited_task_plane=False,
                ),
            ),
            prior_feedback=(),
        )
    )

    payload = context.to_prompt_payload(candidate_index=0)

    assert payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-data-frontier-conformance"
    assert payload["repair_conformance"]["requires_fixture_derived_probe"] is True
    assert payload["repair_conformance"]["late_observed_operations"] == [
        "records.query"
    ]


def test_prompt_payload_prefers_verified_task_rollout_over_sibling_static_conformance() -> None:
    direct = EvaluationSummary(
        variant_id="candidate-task-rollout",
        dataset_split="validation",
        metrics={
            "failed_gates": ["candidate_replay"],
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                }
            ],
            "repair_candidate_package": {
                "candidate_id": "candidate-task-rollout",
                "files": [
                    {"path": "replay/runtime.py", "content": "# task rollout\n"}
                ],
            },
        },
    )
    sibling_static = EvaluationSummary(
        variant_id="candidate-sibling-static",
        dataset_split="validation",
        metrics={
            "failed_gates": ["candidate_replay"],
            "candidate_validation_diagnostics": [
                {
                    "code": "failed_gate",
                    "details": {
                        "stage": "repair_conformance",
                        "code": "late_fixture_probe_not_recorded",
                        "repair_conformance": {
                            "focus_candidate_id": "candidate-parent",
                            "failure_codes": [
                                "implement_observed_endpoint_interactions"
                            ],
                            "interaction_progress": 0,
                            "base_file_fingerprints": {
                                "replay/runtime.py": "sha256:parent"
                            },
                            "required_branch_paths": ["replay/runtime.py"],
                            "base_branch_fingerprints": {},
                            "late_observed_operations": ["records.query"],
                            "requires_fixture_derived_probe": True,
                        },
                    },
                }
            ],
            "repair_candidate_package": {
                "candidate_id": "candidate-sibling-static",
                "files": [
                    {"path": "replay/runtime.py", "content": "# static reject\n"}
                ],
            },
        },
    )

    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(direct, sibling_static),
            prior_feedback=(),
        )
    )

    payload = context.to_prompt_payload(candidate_index=0)
    assert payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-task-rollout"


def test_prompt_payload_prefers_deep_dynamic_conformance_over_newer_static_failure() -> None:
    def conformance_feedback(
        candidate_id: str,
        code: str,
    ) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate_id,
            dataset_split="validation",
            metrics={
                "failed_gates": ["candidate_replay"],
                "interaction_progress": 10,
                "candidate_validation_diagnostics": [
                    {
                        "code": "failed_gate",
                        "stage": "candidate_replay",
                        "details": {
                            "stage": "repair_conformance",
                            "code": code,
                            "repair_conformance": {
                                "focus_candidate_id": "candidate-parent",
                                "failure_codes": [
                                    "verify_declared_protocol_probe_branch"
                                ],
                                "interaction_progress": 10,
                                "base_file_fingerprints": {
                                    "replay/runtime.py": "sha256:old"
                                },
                                "required_branch_paths": [
                                    "replay/runtime.py"
                                ],
                                "base_branch_fingerprints": {},
                            },
                        },
                    }
                ],
                "repair_candidate_package": {
                    "candidate_id": candidate_id,
                    "files": [
                        {
                            "path": "replay/runtime.py",
                            "content": f"# {candidate_id}",
                        }
                    ],
                },
            },
        )

    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(
                conformance_feedback(
                    "candidate-dynamic",
                    "repair_probe_execution_failed",
                ),
                conformance_feedback(
                    "candidate-static-newer",
                    "repair_branch_unchanged",
                ),
            ),
            prior_feedback=(),
        )
    )

    payload = context.to_prompt_payload(candidate_index=0)

    assert payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-dynamic"


def test_prompt_payload_prefers_complete_probe_mismatch_over_handler_abort() -> None:
    def feedback(candidate_id: str, code: str, progress: int) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate_id,
            dataset_split="validation",
            metrics={
                "failed_gates": ["candidate_replay"],
                "interaction_progress": progress,
                "candidate_validation_diagnostics": [
                    {
                        "code": code,
                        "stage": "replay_capability",
                        "reason": "bounded candidate failure",
                    }
                ],
                "repair_candidate_package": {
                    "candidate_id": candidate_id,
                    "files": [
                        {"path": "replay/runtime.py", "content": f"# {candidate_id}"}
                    ],
                },
            },
        )

    context = compile_evolution_context(
        replace(
            _request(),
            validation_feedback=(
                feedback(
                    "candidate-handler-abort",
                    "diagnose_protocol_handler_abort",
                    8,
                ),
                feedback(
                    "candidate-probe-response",
                    "verify_declared_protocol_probe_branch",
                    9,
                ),
            ),
            prior_feedback=(),
        )
    )

    payload = context.to_prompt_payload(candidate_index=0)

    assert payload["repair_focus"]["repair_candidate_package"][
        "candidate_id"
    ] == "candidate-probe-response"


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


def test_compiler_includes_bounded_sanitized_trainable_trace_step_summaries() -> None:
    request = replace(
        _request(),
        trace_packs=(
            TracePack(
                pack_id="trajectory:train-1",
                source_kind="trajectory_log",
                task_id="train-1",
                steps=(
                    TraceEvidenceStep(
                        evidence_id="train-1:step-1",
                        source_index=0,
                        original_id="step-1",
                        state={
                            "input": {
                                "task": "inspect the recorded endpoint",
                                "authorization": "Bearer private-value",
                            },
                            "messages": [
                                {
                                    "role": "tool",
                                    "content": "recorded discovery response",
                                }
                            ],
                        },
                        action={
                            "content": "Call the recorded state tool.",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "mcp",
                                        "arguments": json.dumps(
                                            {
                                                "action_name": "recorded_state",
                                                "params": {"path": "/json/list"},
                                            }
                                        ),
                                    }
                                }
                            ],
                        },
                        reward={"score": 1.0, "note": "completed"},
                        agent_id="main-agent",
                        tool_names=("mcp",),
                    ),
                ),
            ),
        ),
    )

    context = compile_evolution_context(request)

    step = context.trace_evidence[0]["steps"][0]
    assert step == {
        "evidence_id": "train-1:step-1",
        "source_index": 0,
        "agent_id": "main-agent",
        "tool_names": ["mcp"],
        "input_excerpt": (
            '{"authorization":"<REDACTED_SECRET>",'
            '"task":"inspect the recorded endpoint"}'
        ),
        "action_excerpt": "Call the recorded state tool.",
        "observation_excerpt": "recorded discovery response",
        "tool_call_summaries": [
            {
                "name": "mcp",
                "arguments_excerpt": (
                    '{"action_name": "recorded_state", '
                    '"params": {"path": "/json/list"}}'
                ),
            }
        ],
        "reward_summary": {"score": 1.0, "note": "completed"},
    }
    serialized = json.dumps(context.trace_evidence, ensure_ascii=False)
    assert "private-value" not in serialized
    assert len(serialized) < 20_000


def test_compiler_samples_trace_evidence_across_the_full_trajectory() -> None:
    steps = tuple(
        TraceEvidenceStep(
            evidence_id=f"train-1:step-{index}",
            source_index=index,
            original_id=f"step-{index}",
            state={
                "messages": [
                    {
                        "role": "tool",
                        "content": (
                            "recorded data-plane result"
                            if index == 11
                            else f"observation {index}"
                        ),
                    }
                ]
            },
            action={"content": f"action {index}"},
            reward={"score": 1.0 if index == 11 else 0.0},
        )
        for index in range(12)
    )
    request = replace(
        _request(),
        trace_packs=(
            TracePack(
                pack_id="trajectory:train-1",
                source_kind="trajectory_log",
                task_id="train-1",
                steps=steps,
            ),
        ),
    )

    context = compile_evolution_context(request)

    sampled = context.trace_evidence[0]["steps"]
    assert len(sampled) == 8
    assert sampled[0]["source_index"] == 0
    assert sampled[-1]["source_index"] == 11
    assert sampled[-1]["observation_excerpt"] == "recorded data-plane result"


def test_trace_evidence_uses_one_global_budget_across_many_packs() -> None:
    packs = tuple(
        TracePack(
            pack_id=f"trajectory:train-{pack_index}",
            source_kind="trajectory_log",
            task_id=f"train-{pack_index}",
            steps=tuple(
                TraceEvidenceStep(
                    evidence_id=f"train-{pack_index}:step-{step_index}",
                    source_index=step_index,
                    original_id=f"step-{step_index}",
                    state={
                        "messages": [
                            {
                                "role": "tool",
                                "content": "observation-" + ("o" * 2_000),
                            }
                        ]
                    },
                    action={"content": "action-" + ("a" * 2_000)},
                    reward={"detail": "reward-" + ("r" * 2_000)},
                )
                for step_index in range(12)
            ),
        )
        for pack_index in range(32)
    )
    context = compile_evolution_context(
        replace(
            _request(),
            trace_packs=packs,
        )
    )

    serialized = json.dumps(
        context.trace_evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert len(context.trace_evidence) == 32
    assert len(serialized) <= MAX_CONTEXT_TRACE_CHARS + 1_024
    assert context.trace_evidence[0]["evidence_step_ids"] == [
        "train-0:step-0",
        "train-0:step-11",
    ]
