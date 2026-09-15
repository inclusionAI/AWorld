import pytest

from aworld.core.tool.surface import (
    CapabilityMatch,
    CapabilityProbe,
    CapabilityStatus,
    ToolCapabilityEvidence,
    ToolCapabilitySpec,
    ToolLifecycle,
    ToolSurfaceProfile,
    reconcile_tool_surface,
)


def _schema(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_receipt_advertises_only_capabilities_bound_to_live_schemas() -> None:
    specs = (
        ToolCapabilitySpec(
            capability_id="context",
            schema_ids=("CONTEXT_TOOL__list_sessions",),
        ),
        ToolCapabilitySpec(
            capability_id="cast",
            schema_ids=("CAST_SEARCH__search", "CAST_CODER__edit"),
            match=CapabilityMatch.ANY,
        ),
        ToolCapabilitySpec(
            capability_id="terminal",
            schema_ids=("bash",),
            requires_probe=True,
            required=True,
        ),
    )

    receipt = reconcile_tool_surface(
        specs,
        live_tool_schemas=(
            _schema("CONTEXT_TOOL__list_sessions"),
            _schema("CAST_SEARCH__search"),
            _schema("bash"),
        ),
        probes=(
            CapabilityProbe(
                capability_id="terminal",
                succeeded=True,
                probe_id="terminal-pwd-canary-v1",
            ),
        ),
    )

    assert receipt.ready is True
    assert receipt.advertised_capability_ids == ("context", "cast", "terminal")
    assert [item.status for item in receipt.evidence] == [
        CapabilityStatus.AVAILABLE,
        CapabilityStatus.AVAILABLE,
        CapabilityStatus.AVAILABLE,
    ]
    assert receipt.receipt_hash.startswith("sha256:")
    assert type(receipt).from_dict(receipt.to_dict()) == receipt


def test_schema_presence_does_not_replace_required_runtime_canary() -> None:
    spec = ToolCapabilitySpec(
        capability_id="terminal",
        schema_ids=("bash",),
        requires_probe=True,
        required=True,
    )

    missing = reconcile_tool_surface((spec,), live_tool_schemas=(_schema("bash"),))
    failed = reconcile_tool_surface(
        (spec,),
        live_tool_schemas=(_schema("bash"),),
        probes=(
            CapabilityProbe(
                capability_id="terminal",
                succeeded=False,
                probe_id="terminal-pwd-canary-v1",
                reason_code="canary_failed",
            ),
        ),
    )

    assert missing.ready is False
    assert missing.evidence[0].status is CapabilityStatus.PROBE_MISSING
    assert missing.advertised_capability_ids == ()
    assert failed.ready is False
    assert failed.evidence[0].status is CapabilityStatus.PROBE_FAILED


def test_immediate_profile_excludes_cron_and_background_actions_structurally() -> None:
    profile = ToolSurfaceProfile(
        profile_id="immediate-direct-run",
        allowed_lifecycles=(ToolLifecycle.IMMEDIATE,),
    )
    receipt = reconcile_tool_surface(
        (
            ToolCapabilitySpec(
                capability_id="cron",
                schema_ids=("cron__cron_tool",),
                lifecycle=ToolLifecycle.DURABLE,
            ),
            ToolCapabilitySpec(
                capability_id="spawn",
                schema_ids=("async_spawn_subagent__spawn",),
            ),
            ToolCapabilitySpec(
                capability_id="spawn-background",
                schema_ids=("async_spawn_subagent__spawn_background",),
                lifecycle=ToolLifecycle.BACKGROUND,
            ),
        ),
        live_tool_schemas=(
            _schema("cron__cron_tool"),
            _schema("async_spawn_subagent__spawn"),
            _schema("async_spawn_subagent__spawn_background"),
        ),
        profile=profile,
    )

    assert receipt.advertised_capability_ids == ("spawn",)
    assert [item.status for item in receipt.evidence] == [
        CapabilityStatus.PROFILE_EXCLUDED,
        CapabilityStatus.AVAILABLE,
        CapabilityStatus.PROFILE_EXCLUDED,
    ]
    # A required capability intentionally excluded by the selected lifecycle
    # profile does not make registration unhealthy.
    assert receipt.ready is True


def test_duplicate_live_schema_id_is_ambiguous_instead_of_first_wins() -> None:
    receipt = reconcile_tool_surface(
        (
            ToolCapabilitySpec(
                capability_id="terminal",
                schema_ids=("bash",),
                required=True,
            ),
        ),
        live_tool_schemas=(_schema("bash"), _schema("bash")),
    )

    assert receipt.ready is False
    assert receipt.duplicate_schema_ids == ("bash",)
    assert receipt.evidence[0].status is CapabilityStatus.SCHEMA_AMBIGUOUS


def test_invalid_or_description_only_schema_cannot_certify_capability() -> None:
    receipt = reconcile_tool_surface(
        (
            ToolCapabilitySpec(
                capability_id="terminal",
                schema_ids=("bash",),
                required=True,
            ),
        ),
        live_tool_schemas=(
            {"description": "bash is definitely available"},
            {"function": {"name": ""}},
        ),
    )

    assert receipt.ready is False
    assert receipt.malformed_schema_count == 2
    assert receipt.evidence[0].status is CapabilityStatus.SCHEMA_MISSING


def test_specs_require_explicit_schema_identity_and_stable_probe_binding() -> None:
    with pytest.raises(ValueError, match="schema_ids"):
        ToolCapabilitySpec(capability_id="terminal", schema_ids=())
    with pytest.raises(ValueError, match="stable identifier"):
        CapabilityProbe(
            capability_id="terminal",
            succeeded=True,
            probe_id="contains spaces",
        )
    with pytest.raises(TypeError, match="sequence"):
        ToolCapabilitySpec(capability_id="terminal", schema_ids="bash")


def test_receipt_rejects_tampered_projection_and_orphan_probe() -> None:
    receipt = reconcile_tool_surface(
        (ToolCapabilitySpec(capability_id="context", schema_ids=("context__read",)),),
        live_tool_schemas=(_schema("context__read"),),
    )
    tampered = receipt.to_dict()
    tampered["advertised_capability_ids"] = []

    with pytest.raises(ValueError, match="advertised projection"):
        type(receipt).from_dict(tampered)
    with pytest.raises(ValueError, match="unknown capability"):
        reconcile_tool_surface(
            (ToolCapabilitySpec(capability_id="context", schema_ids=("context__read",)),),
            live_tool_schemas=(_schema("context__read"),),
            probes=(
                CapabilityProbe(
                    capability_id="terminal",
                    succeeded=True,
                    probe_id="terminal-canary-v1",
                ),
            ),
        )


def test_evidence_rejects_inconsistent_status_and_schema_partition() -> None:
    with pytest.raises(ValueError, match="partition"):
        ToolCapabilityEvidence(
            capability_id="terminal",
            status=CapabilityStatus.AVAILABLE,
            lifecycle=ToolLifecycle.IMMEDIATE,
            required=True,
            declared_schema_ids=("bash", "history"),
            matched_schema_ids=("bash",),
            missing_schema_ids=(),
        )

    with pytest.raises(ValueError, match="cannot carry a reason_code"):
        ToolCapabilityEvidence(
            capability_id="terminal",
            status=CapabilityStatus.AVAILABLE,
            lifecycle=ToolLifecycle.IMMEDIATE,
            required=True,
            declared_schema_ids=("bash",),
            matched_schema_ids=("bash",),
            missing_schema_ids=(),
            reason_code="claimed_failure",
        )


def test_probe_is_rejected_when_spec_does_not_require_runtime_evidence() -> None:
    with pytest.raises(ValueError, match="does not require"):
        reconcile_tool_surface(
            (ToolCapabilitySpec(capability_id="context", schema_ids=("context__read",)),),
            live_tool_schemas=(_schema("context__read"),),
            probes=(
                CapabilityProbe(
                    capability_id="context",
                    succeeded=True,
                    probe_id="irrelevant-canary-v1",
                ),
            ),
        )
