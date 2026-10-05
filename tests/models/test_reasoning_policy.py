"""Focused contract tests for AWorld-owned phase reasoning selection."""

from dataclasses import FrozenInstanceError

import pytest

from aworld.models.reasoning_policy import (
    AZURE_OPENAI_REASONING_CAPABILITY,
    OPENAI_REASONING_CAPABILITY,
    ReasoningPhase,
    ReasoningPhasePolicy,
    ReasoningPolicyConflictError,
    ReasoningPolicyError,
    ReasoningProfile,
    ReasoningSelectionReceipt,
    resolve_reasoning_request,
)
from aworld.models.reviewed_custom_provider import (
    REVIEWED_CUSTOM_OPENAI_REASONING_CAPABILITY,
)


def _resolve(
    *,
    phase=ReasoningPhase.EXECUTE,
    request=None,
    policy=None,
    provider="openai",
    model="matrixllm.aisearch_dsv41flash",
    reasoning_transport="auto",
    transport_capability=None,
):
    capability_kwargs = (
        {}
        if transport_capability is None
        else {"transport_capability": transport_capability}
    )
    return resolve_reasoning_request(
        phase=phase,
        model_name=model,
        provider=provider,
        request_kwargs=request,
        policy=policy,
        reasoning_transport=reasoning_transport,
        **capability_kwargs,
    )


def test_no_policy_or_caller_pin_preserves_request_without_implicit_default():
    request = {"temperature": 0.2, "extra_body": {"routing": {"tier": "canary"}}}

    resolved, receipt = _resolve(request=request)

    assert resolved == request
    assert resolved is not request
    assert resolved["extra_body"] is request["extra_body"]
    assert receipt == ReasoningSelectionReceipt(
        phase=ReasoningPhase.EXECUTE,
        source="unchanged",
        reasoning_effort=None,
        thinking=None,
        policy_id=None,
        transport=None,
        applied=False,
        reason_code="no_reasoning_selection",
    )


def test_opaque_sdk_kwargs_are_never_deepcopied_with_policy_off_or_on():
    class OpaqueSDKValue:
        def __deepcopy__(self, memo):
            raise AssertionError("opaque SDK value must not be deep-copied")

    opaque = OpaqueSDKValue()
    request = {"response_format": opaque}

    dormant, dormant_receipt = _resolve(model="gpt-4.1", request=request, policy=None)
    active, active_receipt = _resolve(
        model="gpt-4.1",
        request=request,
        policy=ReasoningPhasePolicy.balanced(),
    )

    assert dormant["response_format"] is opaque
    assert dormant_receipt.applied is False
    assert active["response_format"] is opaque
    assert active["reasoning_effort"] == "high"
    assert active_receipt.transport == "openai/v1"


@pytest.mark.parametrize(
    ("phase", "effort"),
    [
        (ReasoningPhase.PLAN, "xhigh"),
        (ReasoningPhase.EXECUTE, "high"),
        (ReasoningPhase.REVIEW, "xhigh"),
        (ReasoningPhase.FINALIZE, "high"),
    ],
)
def test_opt_in_balanced_policy_selects_each_phase(phase, effort):
    resolved, receipt = _resolve(
        phase=phase,
        request={"max_completion_tokens": 32768},
        policy=ReasoningPhasePolicy.balanced(),
    )

    assert resolved["reasoning_effort"] == effort
    assert "extra_body" not in resolved
    assert receipt.source == "phase_policy"
    assert receipt.policy_id == "balanced/v1"
    assert receipt.applied is True
    assert receipt.reason_code == "phase_policy_selected"
    assert receipt.transport == "openai/v1"


@pytest.mark.parametrize("phase", list(ReasoningPhase))
def test_explicit_caller_max_wins_policy_and_maps_to_standard_wire(phase):
    resolved, receipt = _resolve(
        phase=phase,
        request={"reasoning_effort": "MAX"},
        policy=ReasoningPhasePolicy.balanced(),
    )

    assert resolved["reasoning_effort"] == "xhigh"
    assert "extra_body" not in resolved
    assert receipt.source == "caller"
    assert receipt.policy_id is None
    assert receipt.reasoning_effort == "xhigh"
    assert receipt.reason_code == "explicit_caller_pin"


def test_nested_caller_off_overrides_phase_policy_and_is_mirrored_to_openai():
    request = {
        "extra_body": {
            "chat_template_kwargs": {
                "reasoning_effort": "off",
                "thinking": False,
                "tokenizer_option": "keep",
            }
        }
    }

    resolved, receipt = _resolve(
        phase="plan", request=request, policy=ReasoningPhasePolicy.balanced()
    )

    assert resolved["reasoning_effort"] == "off"
    assert resolved["extra_body"]["chat_template_kwargs"] == {
        "reasoning_effort": "off",
        "thinking": False,
        "tokenizer_option": "keep",
    }
    assert receipt.source == "caller"
    assert receipt.thinking is False


def test_projection_deep_merges_without_mutating_caller_objects():
    request = {
        "metadata": {"request": "keep"},
        "extra_body": {
            "routing": {"region": "cn"},
            "chat_template_kwargs": {"custom": {"flag": True}},
        },
    }
    original_template = request["extra_body"]["chat_template_kwargs"]

    resolved, _ = _resolve(
        phase="review", request=request, policy=ReasoningPhasePolicy.balanced()
    )

    assert request == {
        "metadata": {"request": "keep"},
        "extra_body": {
            "routing": {"region": "cn"},
            "chat_template_kwargs": {"custom": {"flag": True}},
        },
    }
    assert resolved["metadata"] is request["metadata"]
    assert resolved["extra_body"]["routing"] is request["extra_body"]["routing"]
    assert resolved["extra_body"]["chat_template_kwargs"] is not original_template
    assert resolved["extra_body"] == {
        "routing": {"region": "cn"},
        "chat_template_kwargs": {
            "custom": {"flag": True},
            "reasoning_effort": "max",
            "thinking": True,
        },
    }


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("anthropic", "dsv4"),
        (None, "dsv4"),
    ],
)
def test_unsupported_transport_fails_open_without_policy_mutation(provider, model):
    request = {"temperature": 0.7, "extra_body": {"keep": [1, 2]}}

    resolved, receipt = _resolve(
        provider=provider,
        model=model,
        phase="plan",
        request=request,
        policy=ReasoningPhasePolicy.balanced(),
    )

    assert resolved == request
    assert receipt.source == "phase_policy"
    assert receipt.reasoning_effort == "max"
    assert receipt.applied is False
    assert receipt.reason_code == "unsupported_reasoning_transport"


def test_explicit_pin_is_preserved_but_not_reprojected_on_unsupported_transport():
    request = {"reasoning_effort": "max", "metadata": {"keep": True}}

    resolved, receipt = _resolve(
        provider="anthropic",
        model="dsv4",
        request=request,
        policy=ReasoningPhasePolicy.balanced(),
    )

    assert resolved == request
    assert "extra_body" not in resolved
    assert receipt.source == "caller"
    assert receipt.reasoning_effort == "max"
    assert receipt.applied is False
    assert receipt.reason_code == "unsupported_reasoning_transport"


@pytest.mark.parametrize("model", ["gpt-4.1", "dsv4", None])
def test_openai_policy_is_model_name_agnostic_and_uses_standard_transport(model):
    resolved, receipt = _resolve(
        model=model,
        phase="execute",
        policy=ReasoningPhasePolicy.balanced(),
    )

    assert resolved["reasoning_effort"] == "high"
    assert "extra_body" not in resolved
    assert receipt.transport == "openai/v1"


def test_azure_auto_and_explicit_custom_openai_transport_are_supported():
    azure, azure_receipt = _resolve(
        provider="azure_openai",
        model="deployment-name",
        policy=ReasoningPhasePolicy.balanced(),
    )
    custom, custom_receipt = _resolve(
        provider="custom",
        model="self-hosted-reasoner",
        policy=ReasoningPhasePolicy.balanced(),
        reasoning_transport="openai",
        transport_capability=REVIEWED_CUSTOM_OPENAI_REASONING_CAPABILITY,
    )

    assert azure["reasoning_effort"] == "high"
    assert azure_receipt.transport == "openai/v1"
    assert custom["reasoning_effort"] == "high"
    assert custom_receipt.transport == "openai/v1"


def test_explicit_chat_template_transport_creates_vendor_mirror():
    resolved, receipt = _resolve(
        model="gpt-4.1-compatible",
        phase="execute",
        policy=ReasoningPhasePolicy.balanced(),
        reasoning_transport="openai_chat_template",
    )

    assert resolved["reasoning_effort"] == "high"
    assert resolved["extra_body"]["chat_template_kwargs"] == {
        "reasoning_effort": "high",
        "thinking": True,
    }
    assert receipt.transport == "openai+chat_template/v1"


@pytest.mark.parametrize(
    ("declaration", "wire_effort"),
    [
        ({"reasoning_effort": "max"}, "xhigh"),
        ({"reasoning_effort": "off"}, "none"),
    ],
)
def test_standard_openai_transport_maps_internal_extremes(declaration, wire_effort):
    resolved, receipt = _resolve(request=declaration, reasoning_transport="openai")

    assert resolved == {"reasoning_effort": wire_effort}
    assert receipt.reasoning_effort == wire_effort
    assert receipt.transport == "openai/v1"
    assert receipt.applied is True


@pytest.mark.parametrize(
    ("declaration", "wire_effort", "thinking"),
    [
        ({"reasoning_effort": "max"}, "max", True),
        ({"reasoning_effort": "off"}, "off", False),
    ],
)
def test_explicit_chat_template_capability_preserves_vendor_extremes(
    declaration, wire_effort, thinking
):
    resolved, receipt = _resolve(
        request=declaration,
        reasoning_transport="openai_chat_template",
        transport_capability=OPENAI_REASONING_CAPABILITY,
    )

    assert resolved["reasoning_effort"] == wire_effort
    assert resolved["extra_body"]["chat_template_kwargs"] == {
        "reasoning_effort": wire_effort,
        "thinking": thinking,
    }
    assert receipt.reasoning_effort == wire_effort
    assert receipt.transport == "openai+chat_template/v1"


def test_explicit_standard_transport_consumes_vendor_reasoning_aliases():
    resolved, receipt = _resolve(
        request={
            "extra_body": {
                "chat_template_kwargs": {
                    "reasoning_effort": "off",
                    "thinking": False,
                    "tokenizer_option": "keep",
                }
            }
        },
        reasoning_transport="openai",
    )

    assert resolved == {
        "reasoning_effort": "none",
        "extra_body": {"chat_template_kwargs": {"tokenizer_option": "keep"}},
    }
    assert receipt.transport == "openai/v1"


def test_explicit_transport_still_requires_matching_adapter_capability():
    mismatched, mismatched_receipt = resolve_reasoning_request(
        phase="plan",
        model_name="irrelevant",
        provider="anthropic",
        request_kwargs={},
        policy=ReasoningPhasePolicy.balanced(),
        reasoning_transport="openai",
        transport_capability=OPENAI_REASONING_CAPABILITY,
    )
    azure, azure_receipt = resolve_reasoning_request(
        phase="plan",
        model_name="deployment",
        provider="azure_openai",
        request_kwargs={},
        policy=ReasoningPhasePolicy.balanced(),
        reasoning_transport="openai_chat_template",
        transport_capability=AZURE_OPENAI_REASONING_CAPABILITY,
    )

    assert mismatched == {}
    assert mismatched_receipt.applied is False
    assert mismatched_receipt.reason_code == "unsupported_reasoning_transport"
    assert azure == {}
    assert azure_receipt.applied is False
    assert azure_receipt.reason_code == "unsupported_reasoning_transport"


def test_reviewed_custom_capability_requires_explicit_standard_transport():
    auto, auto_receipt = resolve_reasoning_request(
        phase="plan",
        model_name="custom-reasoner",
        provider="custom",
        request_kwargs={},
        policy=ReasoningPhasePolicy.balanced(),
        transport_capability=REVIEWED_CUSTOM_OPENAI_REASONING_CAPABILITY,
    )
    explicit, explicit_receipt = resolve_reasoning_request(
        phase="plan",
        model_name="custom-reasoner",
        provider="custom",
        request_kwargs={},
        policy=ReasoningPhasePolicy.balanced(),
        reasoning_transport="openai",
        transport_capability=REVIEWED_CUSTOM_OPENAI_REASONING_CAPABILITY,
    )

    assert auto == {}
    assert auto_receipt.applied is False
    assert auto_receipt.reason_code == "unsupported_reasoning_transport"
    assert explicit == {"reasoning_effort": "xhigh"}
    assert explicit_receipt.applied is True
    assert explicit_receipt.reasoning_effort == "xhigh"


def test_incompatible_extra_body_shape_fails_open_instead_of_overwriting():
    request = {"extra_body": "opaque-provider-value"}

    resolved, receipt = _resolve(
        phase="plan",
        request=request,
        policy=ReasoningPhasePolicy.balanced(),
        reasoning_transport="openai_chat_template",
    )

    assert resolved == request
    assert receipt.applied is False
    assert receipt.reason_code == "incompatible_request_shape"


def test_standard_transport_preserves_opaque_extra_body_instead_of_migrating_template():
    request = {
        "extra_body": "opaque-provider-value",
        "chat_template_kwargs": {"tokenizer_option": "keep"},
    }

    resolved, receipt = _resolve(
        phase="plan",
        request=request,
        policy=ReasoningPhasePolicy.balanced(),
        reasoning_transport="openai",
    )

    assert resolved == request
    assert receipt.applied is False
    assert receipt.reason_code == "incompatible_request_shape"


def test_conflicting_caller_effort_locations_are_rejected():
    request = {
        "reasoning_effort": "max",
        "extra_body": {"chat_template_kwargs": {"reasoning_effort": "high"}},
    }

    with pytest.raises(
        ReasoningPolicyConflictError,
        match="conflicting explicit reasoning_effort",
    ):
        _resolve(request=request, policy=ReasoningPhasePolicy.balanced())


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"reasoning_effort": "high", "thinking": False},
        {
            "reasoning_effort": "off",
            "extra_body": {"chat_template_kwargs": {"thinking": True}},
        },
    ],
)
def test_conflicting_caller_thinking_and_effort_are_rejected(request_kwargs):
    with pytest.raises(ReasoningPolicyConflictError, match="declarations conflict"):
        _resolve(request=request_kwargs)


def test_single_explicit_thinking_switch_gets_openai_effort():
    enabled, enabled_receipt = _resolve(request={"thinking": {"type": "enabled"}})
    disabled, disabled_receipt = _resolve(request={"enable_thinking": False})

    assert enabled["reasoning_effort"] == "xhigh"
    assert "extra_body" not in enabled
    assert enabled_receipt.source == "caller"
    assert disabled["reasoning_effort"] == "none"
    assert "extra_body" not in disabled
    assert disabled_receipt.source == "caller"


def test_explicit_aliases_are_consumed_before_provider_projection():
    resolved, receipt = _resolve(
        request={
            "thinking": {"type": "enabled"},
            "enable_thinking": True,
            "chat_template_kwargs": {"tokenizer_option": "keep"},
            "extra_body": {
                "reasoning_effort": "max",
                "enable_thinking": True,
                "routing": {"tier": "canary"},
            },
        }
    )

    assert receipt.applied is True
    assert "thinking" not in resolved
    assert "enable_thinking" not in resolved
    assert "chat_template_kwargs" not in resolved
    assert "reasoning_effort" not in resolved["extra_body"]
    assert "enable_thinking" not in resolved["extra_body"]
    assert resolved["extra_body"] == {
        "routing": {"tier": "canary"},
        "chat_template_kwargs": {
            "tokenizer_option": "keep",
            "reasoning_effort": "max",
            "thinking": True,
        },
    }


def test_partial_policy_leaves_unselected_phase_unchanged():
    policy = ReasoningPhasePolicy(
        policy_id="review-only/v1",
        review=ReasoningProfile(reasoning_effort="max"),
    )

    resolved, receipt = _resolve(phase="execute", request={}, policy=policy)

    assert resolved == {}
    assert receipt.source == "unchanged"
    assert receipt.reason_code == "no_reasoning_selection"


def test_openai_accepts_generic_phase_effort_without_vendor_mapping():
    policy = ReasoningPhasePolicy(
        policy_id="generic/v1",
        execute=ReasoningProfile(reasoning_effort="medium"),
    )

    resolved, receipt = _resolve(phase="execute", request={}, policy=policy)

    assert resolved == {"reasoning_effort": "medium"}
    assert receipt.applied is True
    assert receipt.transport == "openai/v1"


def test_policy_declaration_normalizes_named_mapping_and_explicit_off():
    assert ReasoningPhasePolicy.from_value(None) is None
    assert ReasoningPhasePolicy.from_value("off") is None
    assert ReasoningPhasePolicy.from_value("balanced").policy_id == "balanced/v1"

    policy = ReasoningPhasePolicy.from_value(
        {
            "policy_id": "custom-throughput/v1",
            "plan": {"reasoning_effort": "max"},
            "execute": {"reasoning_effort": "low"},
        }
    )
    assert policy.policy_id == "custom-throughput/v1"
    assert policy.plan.reasoning_effort == "max"
    assert policy.execute.reasoning_effort == "low"
    assert policy.review is None


@pytest.mark.parametrize(
    "value",
    ["aggressive", {"unknown": {}}, {"plan": {"effort": "max"}}],
)
def test_invalid_policy_declaration_is_rejected(value):
    with pytest.raises(ReasoningPolicyError):
        ReasoningPhasePolicy.from_value(value)


@pytest.mark.parametrize("policy_id", ["x" * 129, "secret value", "\nmax"])
def test_policy_id_is_bounded_content_free_identifier(policy_id):
    with pytest.raises(ReasoningPolicyError, match="bounded safe identifier"):
        ReasoningPhasePolicy(policy_id=policy_id, plan=ReasoningProfile("max"))


def test_policy_profiles_and_receipts_are_immutable_and_content_free():
    policy = ReasoningPhasePolicy.balanced()
    _, receipt = _resolve(phase="plan", policy=policy)

    with pytest.raises(FrozenInstanceError):
        policy.plan.reasoning_effort = "low"
    with pytest.raises(FrozenInstanceError):
        receipt.applied = False
    assert receipt.to_dict() == {
        "phase": "plan",
        "source": "phase_policy",
        "reasoning_effort": "xhigh",
        "thinking": True,
        "policy_id": "balanced/v1",
        "transport": "openai/v1",
        "applied": True,
        "reason_code": "phase_policy_selected",
    }


@pytest.mark.parametrize(
    "profile",
    [
        {"reasoning_effort": None, "thinking": None},
        {"reasoning_effort": "unbounded", "thinking": True},
        {"reasoning_effort": "high", "thinking": False},
        {"reasoning_effort": "off", "thinking": True},
    ],
)
def test_invalid_policy_profiles_are_rejected_at_configuration_time(profile):
    with pytest.raises(ReasoningPolicyError):
        ReasoningProfile(**profile)


@pytest.mark.parametrize("phase", ["", "repair", 1])
def test_unknown_phases_are_rejected(phase):
    with pytest.raises(ReasoningPolicyError, match="phase"):
        _resolve(phase=phase)
