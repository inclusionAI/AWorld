from dataclasses import FrozenInstanceError, replace
import json

import pytest

from aworld.core.execution_protocol import (
    action_signature,
    DeliveryIntent,
    CompletionAssessment,
    ExecutionHorizon,
    EventKind,
    ExecutionProtocolEvent,
    ExecutionProtocolPolicy,
    ExecutionProtocolState,
    ModelExecutionProfile,
    ModelPlanUpdate,
    PlanUpdateDecision,
    ProtocolMode,
    ProtocolScope,
)


def _plan_update(**overrides):
    value = {
        "decision": "replan",
        "horizon": "long",
        "milestone": "produce a runnable candidate",
        "next_action": "run the smallest discriminating probe",
        "next_action_tool": "terminal",
        "next_action_arguments": '{"command":"pytest -q"}',
        "verification_plan": "execute the public smoke test",
        "completion_assessment": "in_progress",
        "delivery_intent": "validate_candidate",
        "delivery_rationale": "the candidate exists and needs a public check",
        "assumptions": ["the public test is representative"],
        "retired_approaches": ["repeat the same failing command"],
        "evidence_refs": ["tool:call-7", "artifact:sha256:abc"],
        "selected_candidate_id": "candidate-2",
    }
    value.update(overrides)
    return value


def test_model_plan_update_has_a_strict_bounded_round_trip():
    update = ModelPlanUpdate.from_model_mapping(_plan_update())

    assert update.decision is PlanUpdateDecision.REPLAN
    assert update.horizon is ExecutionHorizon.LONG
    assert update.completion_assessment is CompletionAssessment.IN_PROGRESS
    assert update.delivery_intent is DeliveryIntent.VALIDATE_CANDIDATE
    assert update.evidence_refs == ("tool:call-7", "artifact:sha256:abc")
    persisted = update.to_dict()
    assert "next_action_arguments" not in persisted
    assert persisted["next_action_signature"] == action_signature(
        "terminal", {"command": "pytest -q"}
    )
    assert "pytest -q" not in json.dumps(persisted)
    assert ModelPlanUpdate.from_persisted_mapping(persisted) == update


def test_model_plan_update_rejects_a_model_supplied_action_signature():
    payload = _plan_update()
    payload.pop("next_action_arguments")
    payload["next_action_signature"] = action_signature(
        "terminal", {"command": "malicious-tool-call"}
    )

    with pytest.raises(ValueError, match="unknown fields"):
        ModelPlanUpdate.from_model_mapping(payload)


@pytest.mark.parametrize(
    "field",
    [
        "delivery_intent",
        "delivery_rationale",
        "next_action_tool",
        "next_action_arguments",
    ],
)
def test_model_plan_update_requires_explicit_delivery_contract(field):
    payload = _plan_update()
    payload.pop(field)

    with pytest.raises(ValueError, match="missing required fields"):
        ModelPlanUpdate.from_model_mapping(payload)


@pytest.mark.parametrize("intent", ["unknown", DeliveryIntent.UNKNOWN])
def test_model_plan_update_rejects_legacy_unknown_intent_from_model(intent):
    with pytest.raises(ValueError, match="delivery_intent must be explicit"):
        ModelPlanUpdate.from_model_mapping(_plan_update(delivery_intent=intent))


def test_persisted_model_plan_update_never_accepts_raw_action_arguments():
    with pytest.raises(ValueError, match="persisted.*unknown fields"):
        ModelPlanUpdate.from_persisted_mapping(_plan_update())


def test_action_signature_is_canonical_and_exported_from_package():
    assert action_signature("terminal__execute", {"b": 2, "a": 1}) == (
        action_signature("terminal__execute", {"a": 1, "b": 2})
    )
    assert action_signature("terminal__execute", {"command": "one"}) != (
        action_signature("terminal__execute", {"command": "two"})
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision", "restart-everything"),
        ("horizon", "maybe"),
        ("milestone", ""),
        ("next_action", "x" * 1025),
        ("next_action_tool", "x" * 257),
        ("verification_plan", ""),
        ("completion_assessment", "complete"),
        ("delivery_intent", "force-a-write"),
        ("delivery_rationale", ""),
        ("delivery_rationale", "x" * 1025),
        ("assumptions", ["x"] * 9),
        ("retired_approaches", ["x" * 513]),
        ("evidence_refs", ["x"] * 17),
        ("selected_candidate_id", "x" * 129),
    ],
)
def test_model_plan_update_rejects_invalid_or_unbounded_claims(field, value):
    with pytest.raises(ValueError):
        ModelPlanUpdate.from_mapping(_plan_update(**{field: value}))


def test_model_plan_update_rejects_unknown_fields():
    with pytest.raises(ValueError, match="unknown fields"):
        ModelPlanUpdate.from_mapping(_plan_update(task_reward=1))


def test_model_plan_update_restores_legacy_persisted_payload():
    payload = _plan_update()
    for field in (
        "delivery_intent",
        "delivery_rationale",
        "next_action_tool",
        "next_action_arguments",
    ):
        payload.pop(field)

    update = ModelPlanUpdate.from_persisted_mapping(payload)

    assert update.delivery_intent is DeliveryIntent.UNKNOWN
    assert update.delivery_rationale == ""
    assert update.next_action_tool is None
    assert update.next_action_signature is None


@pytest.mark.parametrize(
    ("intent", "tool", "arguments"),
    [
        ("continue_exploration", None, None),
        ("produce_candidate", None, None),
        ("validate_candidate", None, None),
        ("submit_current", "terminal", None),
        ("submit_uncertain", "terminal", None),
    ],
)
def test_model_plan_update_requires_tool_identity_exactly_when_action_is_planned(
    intent, tool, arguments
):
    with pytest.raises(ValueError, match="next_action_tool"):
        ModelPlanUpdate.from_mapping(
            _plan_update(
                delivery_intent=intent,
                delivery_rationale="bounded rationale",
                next_action_tool=tool,
                next_action_arguments=arguments,
            )
        )


@pytest.mark.parametrize(
    "arguments",
    [None, "[]", "not-json", '{"number":NaN}'],
)
def test_model_plan_update_requires_exact_json_object_arguments(arguments):
    with pytest.raises(ValueError):
        ModelPlanUpdate.from_mapping(_plan_update(next_action_arguments=arguments))


@pytest.mark.parametrize("intent", ["submit_current", "submit_uncertain"])
def test_terminal_delivery_intent_requires_null_tool_and_arguments(intent):
    update = ModelPlanUpdate.from_mapping(
        _plan_update(
            delivery_intent=intent,
            next_action_tool=None,
            next_action_arguments=None,
        )
    )

    assert update.next_action_tool is None
    assert update.next_action_signature is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("horizon", "maybe"),
        ("confidence", True),
        ("confidence", -0.1),
        ("confidence", 1.1),
        ("milestone_count", 0),
        ("milestone_count", 65),
        ("expected_tool_actions", -1),
        ("expected_tool_actions", 1025),
        ("verification_required", "yes"),
    ],
)
def test_model_execution_profile_rejects_invalid_bounds(field, value):
    values = {
        "horizon": "long",
        "confidence": 0.9,
        "milestone_count": 3,
        "expected_tool_actions": 8,
        "verification_required": True,
        field: value,
    }

    with pytest.raises(ValueError):
        ModelExecutionProfile.from_mapping(values)


def test_model_execution_profile_has_a_stable_typed_round_trip():
    profile = ModelExecutionProfile.from_mapping(
        {
            "horizon": "long",
            "confidence": 0.85,
            "milestone_count": 4,
            "expected_tool_actions": 12,
            "verification_required": True,
            "workspace_mutation_required": True,
        }
    )

    assert profile.horizon is ExecutionHorizon.LONG
    assert profile.workspace_mutation_required is True
    assert ModelExecutionProfile.from_mapping(profile.to_dict()) == profile


def test_model_execution_profile_can_explicitly_remain_unknown():
    profile = ModelExecutionProfile.from_mapping(
        {
            "horizon": "unknown",
            "confidence": 0.0,
            "milestone_count": 1,
            "expected_tool_actions": 0,
            "verification_required": True,
        }
    )

    assert profile.horizon is ExecutionHorizon.UNKNOWN
    assert ModelExecutionProfile.from_mapping(profile.to_dict()) == profile


def test_model_execution_profile_rejects_unknown_fields():
    with pytest.raises(ValueError, match="unknown fields"):
        ModelExecutionProfile.from_mapping(
            {
                "horizon": "long",
                "confidence": 0.9,
                "milestone_count": 3,
                "expected_tool_actions": 8,
                "verification_required": True,
                "task_text": "must never enter control state",
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("history_limit", 0),
        ("history_limit", 257),
        ("activation_event_threshold", 0),
        ("model_activation_confidence_threshold", 1.1),
        ("model_activation_min_milestones", 0),
        ("model_activation_min_milestones", 65),
        ("model_activation_min_tool_actions", 0),
        ("model_activation_min_tool_actions", 1025),
        ("stagnation_event_threshold", True),
        ("max_replans", -1),
        ("max_final_reviews", True),
        ("max_repairs", -1),
        ("finalization_reserve_seconds", -0.1),
        ("finalization_reserve_seconds", float("nan")),
        ("finalization_reserve_seconds", float("inf")),
        ("candidate_decision_reserve_seconds", -0.1),
        ("candidate_decision_reserve_seconds", float("nan")),
        ("candidate_decision_reserve_seconds", float("inf")),
        ("delivery_debt_observation_threshold", 0),
        ("final_review_timeout_seconds", 0),
        ("final_review_timeout_seconds", 86_401),
        ("review_unarmed_candidates", "yes"),
    ],
)
def test_policy_rejects_invalid_bounds(field, value):
    with pytest.raises(ValueError):
        ExecutionProtocolPolicy(**{field: value})


def test_candidate_decision_reserve_must_precede_finalization_or_be_disabled():
    with pytest.raises(ValueError, match="at least finalization"):
        ExecutionProtocolPolicy(
            finalization_reserve_seconds=60,
            candidate_decision_reserve_seconds=30,
        )

    policy = ExecutionProtocolPolicy(
        finalization_reserve_seconds=60,
        candidate_decision_reserve_seconds=0,
    )
    assert policy.candidate_decision_reserve_seconds == 0


def test_policy_coerces_valid_mode_and_is_immutable():
    policy = ExecutionProtocolPolicy(mode="guide")

    assert policy.mode is ProtocolMode.GUIDE
    with pytest.raises(FrozenInstanceError):
        policy.mode = ProtocolMode.OFF


def test_policy_leaves_semantic_attempt_counts_model_owned_by_default():
    policy = ExecutionProtocolPolicy(mode="guide")

    assert policy.max_replans is None
    assert policy.max_final_reviews is None
    assert policy.max_repairs is None
    assert policy.final_review_timeout_seconds is None


def test_policy_accepts_explicit_finite_compatibility_limits():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        max_replans=24,
        max_final_reviews=4,
        max_repairs=3,
    )

    assert policy.max_replans == 24
    assert policy.max_final_reviews == 4
    assert policy.max_repairs == 3


def test_policy_has_a_stable_serialized_round_trip():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        history_limit=9,
        activation_event_threshold=15,
        review_unarmed_candidates=True,
        max_replans=1,
        finalization_reserve_seconds=42.5,
        final_review_timeout_seconds=30,
    )

    assert ExecutionProtocolPolicy.from_dict(policy.to_dict()) == policy
    with pytest.raises(ValueError, match="schema"):
        ExecutionProtocolPolicy.from_dict(
            {**policy.to_dict(), "schema_version": "future"}
        )


def test_policy_restores_pre_activation_v1_with_safe_default():
    payload = ExecutionProtocolPolicy(mode="guide").to_dict()
    payload.pop("activation_event_threshold")
    payload.pop("final_review_timeout_seconds")
    payload.pop("review_unarmed_candidates")

    restored = ExecutionProtocolPolicy.from_dict(payload)

    assert restored.activation_event_threshold == 6
    assert restored.final_review_timeout_seconds is None
    assert restored.review_unarmed_candidates is False


def test_event_validates_generic_measurements_and_review_shape():
    with pytest.raises(ValueError):
        ExecutionProtocolEvent(kind=EventKind.TOOL_OBSERVATION, current_step=-1)
    with pytest.raises(ValueError):
        ExecutionProtocolEvent(kind=EventKind.REVIEW_RESULT)
    with pytest.raises(ValueError):
        ExecutionProtocolEvent(
            kind=EventKind.TOOL_OBSERVATION,
            review_outcome="unknown",
        )
    profile = ModelExecutionProfile(
        horizon=ExecutionHorizon.LONG,
        confidence=0.9,
        milestone_count=3,
        expected_tool_actions=8,
        verification_required=True,
    )
    with pytest.raises(ValueError, match="model_execution_profile"):
        ExecutionProtocolEvent(
            kind=EventKind.TOOL_OBSERVATION,
            model_execution_profile=profile,
        )
    with pytest.raises(ValueError, match="requires"):
        ExecutionProtocolEvent(kind=EventKind.MODEL_EXECUTION_PROFILE)


def test_state_round_trip_contains_no_raw_task_or_tool_text():
    scope = ProtocolScope(task_id="task", task_epoch=3, agent_id="agent")
    event = ExecutionProtocolEvent(
        kind=EventKind.TOOL_OBSERVATION,
        operation_hash="operation-hash",
        result_hash="result-hash",
        current_step=7,
        remaining_seconds=91.5,
    )
    state = replace(
        ExecutionProtocolState.initial(scope).append_event(event, history_limit=4),
        long_horizon_armed=True,
    )

    restored = ExecutionProtocolState.from_dict(state.to_dict())

    assert restored == state
    assert restored.long_horizon_armed is True
    payload = str(state.to_dict())
    assert "operation-hash" in payload
    assert "task_input" not in payload
    assert "tool_output" not in payload


def test_state_round_trip_distinguishes_requested_and_applied_replans():
    state = replace(
        ExecutionProtocolState.initial(
            ProtocolScope(task_id="task", task_epoch=3, agent_id="agent")
        ),
        replan_count=2,
        replan_requested_count=2,
        replan_applied_count=1,
        decision_checkpoint_pending=True,
    )

    restored = ExecutionProtocolState.from_dict(state.to_dict())

    assert restored.replan_count == 2
    assert restored.replan_requested_count == 2
    assert restored.replan_applied_count == 1
    assert restored.decision_checkpoint_pending is True


def test_state_round_trip_preserves_content_free_model_profile():
    scope = ProtocolScope(task_id="task", task_epoch=3, agent_id="agent")
    profile = ModelExecutionProfile(
        horizon=ExecutionHorizon.SHORT,
        confidence=0.8,
        milestone_count=1,
        expected_tool_actions=2,
        verification_required=True,
    )
    state = ExecutionProtocolState.initial(scope).append_event(
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_EXECUTION_PROFILE,
            model_execution_profile=profile,
        ),
        history_limit=4,
    )
    state = replace(state, model_execution_profile=profile)

    restored = ExecutionProtocolState.from_dict(state.to_dict())

    assert restored == state
    assert restored.model_execution_profile == profile
    assert "task_input" not in str(restored.to_dict())


def test_state_rejects_corrupt_persisted_history():
    scope = ProtocolScope(task_id="task", task_epoch=3, agent_id="agent")
    state = ExecutionProtocolState.initial(scope).append_event(
        ExecutionProtocolEvent(kind=EventKind.TOOL_OBSERVATION), history_limit=4
    )
    payload = state.to_dict()
    payload["history"][0]["goal_progress"] = "claimed"

    with pytest.raises(ValueError, match="goal_progress"):
        ExecutionProtocolState.from_dict(payload)
