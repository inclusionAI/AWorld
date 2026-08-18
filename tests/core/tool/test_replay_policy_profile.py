from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json

import pytest

from aworld.core.common import ActionModel
from aworld.core.tool.replay_policy import (
    ArtifactPolicy,
    DynamicEndpointBinding,
    EvidenceContractIdentity,
    EvidenceLifecyclePhase,
    EvidencePolicyValidationError,
    ReplayRuntimePolicy,
    build_framework_evidence_manifest_v2,
    compile_evidence_policy_profile_v2,
    determine_evidence_lifecycle_v2,
    enforce_replay_evidence_runtime_policy,
    evidence_policy_profile_v2_from_environment,
    attest_task_response_v2,
    issue_framework_evidence_writer_attestation_v2,
    issue_producer_registration_capability_v2,
    make_evidence_handle_v2,
    preflight_evidence_policy_v2,
)


_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_DIGEST_C = "sha256:" + "c" * 64


def _artifact_policy(
    artifact_type: str = "browser.snapshot",
    *,
    required: bool = True,
    max_files: int = 2,
    max_items: int = 4,
    max_bytes: int = 1_000_000,
    evaluator_projection_byte_limit: int = 64_000,
) -> ArtifactPolicy:
    return ArtifactPolicy(
        artifact_type=artifact_type,
        registered_producers=("browser.snapshotter",),
        max_files=max_files,
        max_items=max_items,
        max_bytes=max_bytes,
        projection="summary",
        projection_byte_limit=evaluator_projection_byte_limit,
        required=required,
    )


def _profile():
    return compile_evidence_policy_profile_v2(
        artifact_policies=(_artifact_policy(),),
        endpoint_bindings=(
            DynamicEndpointBinding(
                binding_id="browser.debug",
                service_identity="browser.runtime",
                endpoint="ws://127.0.0.1:4100/devtools/",
            ),
        ),
        required_task_response_fields=("status", "summary"),
        allowed_control_actions=("browser:close",),
    )


def _handle(
    handle_id: str = "snapshot.one",
    *,
    byte_count: int = 12_000,
    projection_relative_path: str | None = None,
    projection_digest: str | None = None,
):
    return make_evidence_handle_v2(
        handle_id=handle_id,
        artifact_type="browser.snapshot",
        producer_id="browser.snapshotter",
        relative_path=f"evidence/{handle_id}.json",
        content_digest=_DIGEST_A if handle_id.endswith("one") else _DIGEST_B,
        byte_count=byte_count,
        item_count=1,
        projection_relative_path=projection_relative_path,
        projection_digest=projection_digest,
    )


def _real_handle(
    artifact_root,
    handle_id: str = "snapshot.one",
    *,
    content: bytes = b"bounded snapshot",
    producer_id: str = "browser.snapshotter",
    projection: bytes | None = None,
):
    relative_path = f"evidence/{handle_id}.json"
    path = artifact_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    projection_path = None
    projection_digest = None
    if projection is not None:
        projection_path = f"projections/{handle_id}.txt"
        target = artifact_root / projection_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(projection)
        projection_digest = "sha256:" + hashlib.sha256(projection).hexdigest()
    return make_evidence_handle_v2(
        handle_id=handle_id,
        artifact_type="browser.snapshot",
        producer_id=producer_id,
        relative_path=relative_path,
        content_digest="sha256:" + hashlib.sha256(content).hexdigest(),
        byte_count=len(content),
        item_count=1,
        projection_relative_path=projection_path,
        projection_digest=projection_digest,
    )


def _writer(profile):
    return issue_framework_evidence_writer_attestation_v2(
        profile,
        writer_identity="framework.manifest-writer",
        isolation_identity="isolation.replay-lane",
        resource_identity="resource.browser-runtime",
    )


def _capabilities(profile, writer):
    return (
        issue_producer_registration_capability_v2(
            profile,
            writer,
            producer_id="browser.snapshotter",
            artifact_roots={"browser.snapshot": "evidence"},
        ),
    )


def _build_manifest(profile, handles, task_response, artifact_root):
    writer = _writer(profile)
    return build_framework_evidence_manifest_v2(
        profile,
        handles,
        task_response,
        artifact_root=artifact_root,
        writer_attestation=writer,
        producer_capabilities=_capabilities(profile, writer),
        task_response_attestation=attest_task_response_v2(
            profile, writer, task_response
        ),
    )


def test_profile_is_immutable_canonical_and_order_independent() -> None:
    snapshot = _artifact_policy()
    trace = ArtifactPolicy(
        artifact_type="browser.trace",
        registered_producers=("browser.trace-writer",),
        max_files=1,
        max_items=1,
        max_bytes=200_000,
        projection_byte_limit=32_000,
    )
    first = compile_evidence_policy_profile_v2(
        artifact_policies=(trace, snapshot),
        required_task_response_fields=("summary", "status"),
    )
    second = compile_evidence_policy_profile_v2(
        artifact_policies=(snapshot, trace),
        required_task_response_fields=("status", "summary"),
    )

    assert first.fingerprint == second.fingerprint
    assert [item.artifact_type for item in first.artifact_policies] == [
        "browser.snapshot",
        "browser.trace",
    ]
    with pytest.raises(FrozenInstanceError):
        first.max_consecutive_failed_actions = 9  # type: ignore[misc]


def test_profile_validation_rejects_duplicate_and_unbounded_contract() -> None:
    invalid = ArtifactPolicy(
        artifact_type="browser.snapshot",
        registered_producers=(),
        max_files=0,
        max_items=1,
        max_bytes=1_000,
        projection_byte_limit=2_000,
    )

    with pytest.raises(EvidencePolicyValidationError) as raised:
        compile_evidence_policy_profile_v2(
            artifact_policies=(invalid, invalid),
        )

    codes = {issue.code for issue in raised.value.issues}
    assert "duplicate_artifact_type" in codes
    assert "invalid_producers" in codes
    assert "invalid_budget" in codes
    assert "projection_exceeds_budget" in codes


def test_dynamic_endpoint_binding_is_bound_to_fingerprint() -> None:
    first = _profile()
    second = compile_evidence_policy_profile_v2(
        artifact_policies=(_artifact_policy(),),
        endpoint_bindings=(
            DynamicEndpointBinding(
                binding_id="browser.debug",
                service_identity="browser.runtime",
                endpoint="ws://127.0.0.1:4200/devtools",
            ),
        ),
        required_task_response_fields=("status", "summary"),
        allowed_control_actions=("browser:close",),
    )

    assert first.fingerprint != second.fingerprint
    binding = first.endpoint_bindings[0]
    assert binding.endpoint == "ws://127.0.0.1:4100/devtools"
    assert binding.authority == "127.0.0.1:4100"


def test_scratch_budget_is_frozen_and_cannot_be_smaller_than_trusted_source() -> None:
    first = _profile()
    second = compile_evidence_policy_profile_v2(
        artifact_policies=(_artifact_policy(),),
        endpoint_bindings=first.endpoint_bindings,
        required_task_response_fields=("status", "summary"),
        allowed_control_actions=("browser:close",),
        scratch_max_files=first.scratch_max_files + 1,
        scratch_max_bytes=first.scratch_max_bytes + 1,
    )

    assert first.fingerprint != second.fingerprint
    assert ReplayRuntimePolicy.from_profile(second).artifact_byte_limit == (
        second.scratch_max_bytes
    )
    with pytest.raises(EvidencePolicyValidationError) as raised:
        compile_evidence_policy_profile_v2(
            artifact_policies=(_artifact_policy(),),
            scratch_max_files=1,
            scratch_max_bytes=999_999,
        )
    assert "scratch_below_artifact_byte_budget" in {
        issue.code for issue in raised.value.issues
    }


def test_compiler_input_contract_identities_are_canonical_and_authoritative() -> None:
    first = compile_evidence_policy_profile_v2(
        artifact_policies=(_artifact_policy(),),
        contract_identities=(
            EvidenceContractIdentity("target_adapter", _DIGEST_A),
            EvidenceContractIdentity("evaluator", _DIGEST_B),
        ),
    )
    reordered = compile_evidence_policy_profile_v2(
        artifact_policies=(_artifact_policy(),),
        contract_identities=(
            EvidenceContractIdentity("evaluator", _DIGEST_B),
            EvidenceContractIdentity("target_adapter", _DIGEST_A),
        ),
    )
    drifted = compile_evidence_policy_profile_v2(
        artifact_policies=(_artifact_policy(),),
        contract_identities=(
            EvidenceContractIdentity("evaluator", _DIGEST_C),
            EvidenceContractIdentity("target_adapter", _DIGEST_A),
        ),
    )

    assert first == reordered
    assert first.fingerprint != drifted.fingerprint
    assert first.public_projection()["contract_identity_count"] == 2
    assert type(first).from_dict(first.to_dict()) == first

    with pytest.raises(EvidencePolicyValidationError) as raised:
        compile_evidence_policy_profile_v2(
            artifact_policies=(_artifact_policy(),),
            contract_identities=(
                EvidenceContractIdentity("evaluator", _DIGEST_A),
                EvidenceContractIdentity("evaluator", _DIGEST_B),
            ),
        )
    assert {item.code for item in raised.value.issues} == {
        "duplicate_contract_kind"
    }


def test_environment_round_trip_and_public_projection_are_bounded() -> None:
    profile = _profile()
    environment = profile.to_environment()

    restored = evidence_policy_profile_v2_from_environment(environment)
    assert restored == profile
    assert environment["AWORLD_REPLAY_ENDPOINT_BROWSER_DEBUG"] == (
        "ws://127.0.0.1:4100/devtools"
    )
    runtime = ReplayRuntimePolicy.from_profile(profile)
    assert runtime.artifact_file_limit == profile.scratch_max_files
    assert runtime.artifact_byte_limit == profile.scratch_max_bytes
    assert runtime.allowed_loopback_endpoints == frozenset(
        {"127.0.0.1:4100"}
    )

    public = profile.public_projection()
    assert "4100" not in str(public)
    assert "browser.snapshotter" not in str(public)
    assert public["endpoint_binding_count"] == 1
    assert public["scratch_max_files"] == profile.scratch_max_files
    assert public["scratch_max_bytes"] == profile.scratch_max_bytes
    assert "endpoint_binding_fingerprints" not in public


def test_legacy_runtime_policy_reads_v2_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile()
    environment = profile.to_environment()
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    runtime = ReplayRuntimePolicy.from_environment()

    assert runtime.artifact_file_limit == profile.scratch_max_files
    assert runtime.allowed_loopback_endpoints == frozenset(
        {"127.0.0.1:4100"}
    )
    assert evidence_policy_profile_v2_from_environment({}) is None


def test_environment_profile_fails_closed_on_fingerprint_drift() -> None:
    environment = _profile().to_environment()
    environment["AWORLD_REPLAY_EVIDENCE_POLICY_FINGERPRINT"] = _DIGEST_C

    with pytest.raises(EvidencePolicyValidationError) as raised:
        evidence_policy_profile_v2_from_environment(environment)

    assert raised.value.issues[0].code == "fingerprint_mismatch"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("required", "false"),
        ("max_files", True),
    ),
)
def test_profile_reader_rejects_coerced_bool_and_integer(field, value) -> None:
    payload = _profile().to_dict()
    payload["artifact_policies"][0][field] = value

    with pytest.raises((EvidencePolicyValidationError, ValueError)):
        type(_profile()).from_dict(payload)


def test_profile_reader_requires_real_arrays() -> None:
    payload = _profile().to_dict()
    payload["artifact_policies"] = {"not": "an array"}

    with pytest.raises((EvidencePolicyValidationError, ValueError)):
        type(_profile()).from_dict(payload)


def test_framework_manifest_is_payload_free_and_deterministic(tmp_path) -> None:
    profile = _profile()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    handle = _real_handle(artifact_root)

    manifest = _build_manifest(
        profile,
        (handle,),
        {"status": "succeeded", "summary": "private response", "raw": "secret"},
        artifact_root,
    )

    assert manifest["evidence_policy_fingerprint"] == profile.fingerprint
    assert manifest["task_response_fields"] == ["status", "summary"]
    assert "private response" not in str(manifest)
    assert "secret" not in str(manifest)
    assert handle.to_dict() in manifest["handles"]


def test_manifest_rejects_unregistered_producer_and_per_type_budget(tmp_path) -> None:
    profile = compile_evidence_policy_profile_v2(
        artifact_policies=(
            _artifact_policy(
                max_files=1,
                max_items=1,
                max_bytes=20_000,
                evaluator_projection_byte_limit=10_000,
            ),
        ),
        required_task_response_fields=("status",),
    )
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    unauthorized = _real_handle(
        artifact_root, "snapshot.bad", producer_id="shell.unknown"
    )

    with pytest.raises(EvidencePolicyValidationError) as unauthorized_error:
        _build_manifest(
            profile,
            (unauthorized,),
            {"status": "succeeded"},
            artifact_root,
        )
    assert "unregistered_producer" in {
        issue.code for issue in unauthorized_error.value.issues
    }

    with pytest.raises(EvidencePolicyValidationError) as budget_error:
        _build_manifest(
            profile,
            (
                _real_handle(artifact_root, "snapshot.one", content=b"a" * 12_000),
                _real_handle(artifact_root, "snapshot.two", content=b"b" * 12_000),
            ),
            {"status": "succeeded"},
            artifact_root,
        )
    codes = {issue.code for issue in budget_error.value.issues}
    assert "artifact_file_budget_exceeded" in codes
    assert "artifact_item_budget_exceeded" in codes
    assert "artifact_byte_budget_exceeded" in codes


def test_large_artifact_requires_deterministic_bounded_projection(tmp_path) -> None:
    profile = _profile()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    large = _real_handle(artifact_root, content=b"x" * 100_000)

    with pytest.raises(EvidencePolicyValidationError) as raised:
        _build_manifest(
            profile,
            (large,),
            {"status": "succeeded", "summary": "done"},
            artifact_root,
        )
    assert "bounded_projection_required" in {
        issue.code for issue in raised.value.issues
    }

    projected = _real_handle(
        artifact_root,
        content=b"x" * 100_000,
        projection=b"bounded projection",
    )
    manifest = _build_manifest(
        profile,
        (projected,),
        {"status": "succeeded", "summary": "done"},
        artifact_root,
    )
    assert manifest["handles"] == [projected.to_dict()]


def test_preflight_checks_directory_producer_and_dynamic_endpoint(tmp_path) -> None:
    profile = _profile()
    missing = preflight_evidence_policy_v2(
        profile,
        artifact_root=tmp_path / "missing",
        available_producers=(),
        resolved_endpoint_bindings={},
    )

    assert not missing.passed
    assert {issue.code for issue in missing.issues} == {
        "artifact_directory_unavailable",
        "required_producer_unavailable",
        "endpoint_binding_mismatch",
    }
    ownership = {issue.code: issue.ownership for issue in missing.issues}
    assert ownership["artifact_directory_unavailable"] == "infrastructure"
    assert ownership["required_producer_unavailable"] == "measurement"
    assert ownership["endpoint_binding_mismatch"] == "infrastructure"
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    passed = preflight_evidence_policy_v2(
        profile,
        artifact_root=artifact_root,
        available_producers=("browser.snapshotter",),
        resolved_endpoint_bindings={
            "browser.debug": "ws://127.0.0.1:4100/devtools/"
        },
    )
    assert passed.passed
    assert passed.issues == ()


def test_evidence_lifecycle_transitions_are_explicit() -> None:
    profile = _profile()
    collecting = determine_evidence_lifecycle_v2(
        profile,
        handles=(),
        task_response={"status": "succeeded"},
    )
    assert collecting.phase is EvidenceLifecyclePhase.COLLECTING
    assert not collecting.stop_task_tool_calls
    assert collecting.missing_requirements == (
        "artifact:browser.snapshot",
        "task_response:summary",
    )

    ready = determine_evidence_lifecycle_v2(
        profile,
        handles=(_handle(),),
        task_response={"status": "succeeded", "summary": "done"},
    )
    assert ready.phase is EvidenceLifecyclePhase.EVIDENCE_READY
    assert ready.stop_task_tool_calls

    finalizing = determine_evidence_lifecycle_v2(
        profile,
        handles=(_handle(),),
        task_response={"status": "succeeded", "summary": "done"},
        finalization_started=True,
    )
    assert finalizing.phase is EvidenceLifecyclePhase.FINALIZING
    assert finalizing.stop_task_tool_calls


@pytest.mark.parametrize(
    "relative_path",
    (
        r"C:\\outside\\evidence.json",
        "evidence/file.json:secret",
        "evidence/CON.json",
        "evidence/trailing. /file.json",
    ),
)
def test_handle_rejects_windows_traversal(relative_path: str) -> None:
    with pytest.raises(EvidencePolicyValidationError):
        make_evidence_handle_v2(
            handle_id="snapshot.bad",
            artifact_type="browser.snapshot",
            producer_id="browser.snapshotter",
            relative_path=relative_path,
            content_digest=_DIGEST_A,
            byte_count=1,
        )


def test_manifest_verifies_real_file_digest_and_rejects_symlink(tmp_path) -> None:
    profile = _profile()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    handle = _real_handle(artifact_root)
    handle = make_evidence_handle_v2(
        **{**handle.to_dict(), "content_digest": _DIGEST_A}
    )

    with pytest.raises(EvidencePolicyValidationError) as digest_error:
        _build_manifest(
            profile,
            (handle,),
            {"status": "ok", "summary": "ok"},
            artifact_root,
        )
    assert "artifact_digest_mismatch" in {
        issue.code for issue in digest_error.value.issues
    }

    source = artifact_root / (handle.relative_path or "")
    source.unlink()
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"bounded snapshot")
    source.symlink_to(outside)
    with pytest.raises(EvidencePolicyValidationError) as symlink_error:
        _build_manifest(
            profile,
            (handle,),
            {"status": "ok", "summary": "ok"},
            artifact_root,
        )
    assert "artifact_file_unavailable" in {
        issue.code for issue in symlink_error.value.issues
    }


def _runtime_action(endpoint: str, *, padding: str = "") -> ActionModel:
    return ActionModel(
        tool_name="browser",
        action_name="open",
        tool_call_id="runtime-v2",
        params={"command": padding + endpoint},
    )


class _RuntimeOwner:
    pass


def _install_required_runtime(
    monkeypatch: pytest.MonkeyPatch,
    profile,
    artifact_root,
    manifest_path,
) -> None:
    (artifact_root / "evidence").mkdir(exist_ok=True)
    writer = _writer(profile)
    environment = profile.to_environment(
        writer_attestation=writer,
        producer_capabilities=_capabilities(profile, writer),
        resource_ownership_token="owned-runtime-token",
    )
    environment.update(
        {
            "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR": str(artifact_root),
            "AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST": str(manifest_path),
        }
    )
    for name, value in environment.items():
        monkeypatch.setenv(name, value)


def test_required_runtime_fails_closed_when_contract_environment_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AWORLD_REPLAY_EVIDENCE_POLICY", "1")
    monkeypatch.setenv("AWORLD_REPLAY_EVIDENCE_POLICY_MODE", "required")
    action = _runtime_action("ws://127.0.0.1:4100/devtools")
    assert enforce_replay_evidence_runtime_policy("browser", (action,), _RuntimeOwner()) == (
        "evidence_policy_artifact_root_missing"
    )
    root = tmp_path / "artifacts"
    root.mkdir()
    monkeypatch.setenv("AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR", str(root))
    assert enforce_replay_evidence_runtime_policy("browser", (action,), _RuntimeOwner()) == (
        "evidence_policy_manifest_path_missing"
    )
    monkeypatch.setenv(
        "AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST", str(root / "manifest.json")
    )
    assert enforce_replay_evidence_runtime_policy("browser", (action,), _RuntimeOwner()) == (
        "evidence_policy_profile_missing"
    )


def test_required_runtime_rejects_legacy_manifest_and_enforces_endpoint_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    profile = _profile()
    root = tmp_path / "artifacts"
    root.mkdir()
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        '{"source_id":"legacy","extraction_method":"inline",'
        '"summary":"not-v2"}\n',
        encoding="utf-8",
    )
    _install_required_runtime(monkeypatch, profile, root, manifest_path)

    assert enforce_replay_evidence_runtime_policy(
        "browser", (_runtime_action("ws://127.0.0.1:4100/devtools"),), _RuntimeOwner()
    ) == "evidence_manifest_v2_invalid"

    manifest_path.unlink()
    assert enforce_replay_evidence_runtime_policy(
        "browser", (_runtime_action("ws://127.0.0.1:4100/other"),), _RuntimeOwner()
    ) == "undeclared_loopback_endpoint"
    assert enforce_replay_evidence_runtime_policy(
        "browser", (_runtime_action("ws://127.0.0.1:4100/devtools/session"),), _RuntimeOwner()
    ) is None


def test_required_runtime_uses_verified_v2_manifest_for_evidence_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    profile = _profile()
    root = tmp_path / "artifacts"
    root.mkdir()
    handle = _real_handle(root)
    manifest = _build_manifest(
        profile,
        (handle,),
        {"status": "ok", "summary": "ok", "secret": "excluded"},
        root,
    )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _install_required_runtime(monkeypatch, profile, root, manifest_path)

    assert enforce_replay_evidence_runtime_policy(
        "browser", (_runtime_action("ws://127.0.0.1:4100/devtools"),), _RuntimeOwner()
    ) == "tool_call_after_evidence_ready"


def test_shadow_runtime_records_but_does_not_enforce_evidence_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    manifest_path = root / "evidence_manifest.jsonl"
    manifest_path.write_text(
        json.dumps(
            {
                "source_id": "candidate-advisory",
                "extraction_method": "bounded_extract",
                "summary": "untrusted child annotation",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AWORLD_REPLAY_EVIDENCE_POLICY", "1")
    monkeypatch.setenv("AWORLD_REPLAY_EVIDENCE_POLICY_MODE", "shadow")
    monkeypatch.setenv(
        "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR", str(root)
    )
    monkeypatch.setenv(
        "AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST", str(manifest_path)
    )

    assert enforce_replay_evidence_runtime_policy(
        "browser",
        (_runtime_action("ws://127.0.0.1:4100/devtools"),),
        _RuntimeOwner(),
    ) is None

    state = json.loads(
        (root / "framework_evidence_state.json").read_text(encoding="utf-8")
    )
    assert state["evidence_policy_mode"] == "shadow"
    assert state["evidence_policy_authority"] == "advisory"
    violations = [
        json.loads(line)
        for line in (
            root / "framework_evidence_policy.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert violations[0]["code"] == "tool_call_after_evidence_ready"
    assert violations[0]["evidence_policy_authority"] == "advisory"


def test_required_runtime_enforces_per_type_manifest_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    profile = compile_evidence_policy_profile_v2(
        artifact_policies=(
            _artifact_policy(max_files=1, max_items=1),
        ),
        required_task_response_fields=("status",),
    )
    root = tmp_path / "artifacts"
    root.mkdir()
    handles = (
        _real_handle(root, "snapshot.one"),
        _real_handle(root, "snapshot.two"),
    )
    manifest_path = root / "evidence_manifest.jsonl"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "aworld.evidence_manifest.v2",
                "evidence_policy_fingerprint": profile.fingerprint,
                "handles": [handle.to_dict() for handle in handles],
                "task_response_fields": ["status"],
            }
        ),
        encoding="utf-8",
    )
    _install_required_runtime(monkeypatch, profile, root, manifest_path)

    assert enforce_replay_evidence_runtime_policy(
        "browser",
        (_runtime_action("ws://127.0.0.1:4100/devtools"),),
        _RuntimeOwner(),
    ) == "evidence_manifest_v2_invalid"


def test_oversized_action_parameters_fail_closed_without_endpoint_truncation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    profile = _profile()
    root = tmp_path / "artifacts"
    root.mkdir()
    manifest_path = root / "manifest.json"
    _install_required_runtime(monkeypatch, profile, root, manifest_path)

    result = enforce_replay_evidence_runtime_policy(
        "browser",
        (
            _runtime_action(
                "ws://127.0.0.1:9999/hidden",
                padding="x" * 270_000,
            ),
        ),
        _RuntimeOwner(),
    )
    assert result == "action_parameters_uninspectable"


def test_manifest_requires_matching_framework_task_response_attestation(
    tmp_path,
) -> None:
    profile = _profile()
    root = tmp_path / "artifacts"
    root.mkdir()
    handle = _real_handle(root)
    writer = _writer(profile)

    with pytest.raises(EvidencePolicyValidationError) as raised:
        build_framework_evidence_manifest_v2(
            profile,
            (handle,),
            {"status": "ok", "summary": "actual"},
            artifact_root=root,
            writer_attestation=writer,
            producer_capabilities=_capabilities(profile, writer),
            task_response_attestation=attest_task_response_v2(
                profile, writer, {"status": "ok", "summary": "different"}
            ),
        )

    assert raised.value.issues[0].code == "task_response_attestation_mismatch"


def test_required_runtime_runs_endpoint_and_producer_preflight_before_rollout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    profile = _profile()
    root = tmp_path / "artifacts"
    root.mkdir()
    manifest_path = root / "manifest.json"
    _install_required_runtime(monkeypatch, profile, root, manifest_path)
    (root / "evidence").rmdir()

    assert enforce_replay_evidence_runtime_policy(
        "browser", (_runtime_action("ws://127.0.0.1:4100/devtools"),), _RuntimeOwner()
    ) == "evidence_policy_preflight_failed"

    (root / "evidence").mkdir()
    monkeypatch.setenv(
        "AWORLD_REPLAY_ENDPOINT_BROWSER_DEBUG", "ws://127.0.0.1:4101/devtools"
    )
    assert enforce_replay_evidence_runtime_policy(
        "browser", (_runtime_action("ws://127.0.0.1:4100/devtools"),), _RuntimeOwner()
    ) == "evidence_policy_preflight_failed"


def test_required_runtime_fails_closed_for_symlinked_control_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    profile = _profile()
    root = tmp_path / "artifacts"
    root.mkdir()
    manifest_path = root / "manifest.json"
    _install_required_runtime(monkeypatch, profile, root, manifest_path)
    outside = tmp_path / "outside.json"
    outside.write_text('{"protected":true}', encoding="utf-8")
    (root / "framework_evidence_state.json").symlink_to(outside)

    assert enforce_replay_evidence_runtime_policy(
        "browser", (_runtime_action("ws://127.0.0.1:4100/devtools"),), _RuntimeOwner()
    ) == "evidence_policy_state_invalid"
    assert json.loads(outside.read_text(encoding="utf-8")) == {"protected": True}


def test_required_runtime_fails_closed_for_symlinked_violation_journal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    profile = _profile()
    root = tmp_path / "artifacts"
    root.mkdir()
    manifest_path = root / "manifest.json"
    _install_required_runtime(monkeypatch, profile, root, manifest_path)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("protected\n", encoding="utf-8")
    (root / "framework_evidence_policy.jsonl").symlink_to(outside)

    assert enforce_replay_evidence_runtime_policy(
        "browser", (_runtime_action("ws://127.0.0.1:9999/devtools"),), _RuntimeOwner()
    ) == "evidence_policy_violation_persistence_failed"
    assert outside.read_text(encoding="utf-8") == "protected\n"


def test_required_runtime_inventory_uses_registered_per_type_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    profile = compile_evidence_policy_profile_v2(
        artifact_policies=(_artifact_policy(max_files=1),),
        endpoint_bindings=_profile().endpoint_bindings,
    )
    root = tmp_path / "artifacts"
    root.mkdir()
    manifest_path = root / "manifest.json"
    _install_required_runtime(monkeypatch, profile, root, manifest_path)
    (root / "evidence" / "one.json").write_text("{}", encoding="utf-8")
    (root / "evidence" / "two.json").write_text("{}", encoding="utf-8")

    assert enforce_replay_evidence_runtime_policy(
        "browser", (_runtime_action("ws://127.0.0.1:4100/devtools"),), _RuntimeOwner()
    ) == "artifact_file_budget_exceeded"


def test_endpoint_scope_is_exact_and_numeric_aliases_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    profile = compile_evidence_policy_profile_v2(
        artifact_policies=(_artifact_policy(required=False),),
        endpoint_bindings=(
            DynamicEndpointBinding(
                binding_id="browser.debug",
                service_identity="browser.runtime",
                endpoint="ws://127.0.0.1:4100/devtools",
                path_scope="exact",
            ),
        ),
    )
    root = tmp_path / "artifacts"
    root.mkdir()
    manifest_path = root / "manifest.json"
    _install_required_runtime(monkeypatch, profile, root, manifest_path)

    assert enforce_replay_evidence_runtime_policy(
        "browser", (_runtime_action("ws://127.0.0.1:4100/devtools"),), _RuntimeOwner()
    ) is None
    assert enforce_replay_evidence_runtime_policy(
        "browser",
        (_runtime_action("ws://127.0.0.1:4100/devtools/session"),),
        _RuntimeOwner(),
    ) == "undeclared_loopback_endpoint"
    assert enforce_replay_evidence_runtime_policy(
        "browser", (_runtime_action("http://2130706433:4100/devtools"),), _RuntimeOwner()
    ) == "undeclared_loopback_endpoint"


def test_required_cleanup_is_durable_and_bound_to_resource_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    profile = _profile()
    root = tmp_path / "artifacts"
    root.mkdir()
    handle = _real_handle(root)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            _build_manifest(
                profile,
                (handle,),
                {"status": "ok", "summary": "done"},
                root,
            )
        ),
        encoding="utf-8",
    )
    _install_required_runtime(monkeypatch, profile, root, manifest_path)
    cleanup = ActionModel(
        tool_name="bash",
        action_name="run",
        tool_call_id="cleanup",
        params={
            "command": "agent-browser close",
            "resource_ownership_token": "owned-runtime-token",
            "isolation_identity": "isolation.replay-lane",
            "resource_identity": "resource.browser-runtime",
        },
    )

    assert enforce_replay_evidence_runtime_policy(
        "bash", (cleanup,), _RuntimeOwner()
    ) is None
    assert enforce_replay_evidence_runtime_policy(
        "bash", (cleanup,), _RuntimeOwner()
    ) == "tool_call_after_evidence_ready"


def test_profile_rejects_unsafe_public_version_identity() -> None:
    with pytest.raises(EvidencePolicyValidationError) as raised:
        compile_evidence_policy_profile_v2(
            artifact_policies=(_artifact_policy(),),
            redaction_version="private\nvalue",
        )

    assert "unsafe_policy_version" in {
        issue.code for issue in raised.value.issues
    }
