"""Replay-adaptation failure and contract diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from aworld.self_evolve.failure_events import (
    FailureEventSource,
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureEvent,
)
from aworld.self_evolve.replay_adaptation import (
    ReplayAdaptationBundle,
)
from aworld.self_evolve.replay_capability import (
    REPLAY_CAPABILITY_PROTOCOL_VERSION,
    REPLAY_CAPABILITY_RESULT_SCHEMA_VERSION,
    REPLAY_CAPABILITY_SCHEMA_VERSION,
    REPLAY_CAPABILITY_SUPPORTED_READINESS_KINDS,
    REPLAY_CAPABILITY_SUPPORTED_REQUIREMENT_KINDS,
    REPLAY_CAPABILITY_SUPPORTED_SERVICE_TRANSPORTS,
    ReplayCapabilityError,
)
from aworld.self_evolve.sanitization import sanitize_text
from aworld.self_evolve.schema_diagnostics import (
    _schema_field_contract_fingerprint,
)


def _replay_adaptation_exception_details(
    exc: Exception,
    *,
    candidate_capability: bool,
    schema_field_contract_fingerprint: Callable[[object], str | None] | None = (
        _schema_field_contract_fingerprint
    ),
) -> dict[str, object]:
    reason = sanitize_text(str(exc), max_chars=240)
    if candidate_capability:
        diagnostic: dict[str, object] = {
            "code": "invalid_replay_capability_compile",
            "stage": "capability_compile",
            "failure_class": "candidate",
            "repairable": True,
            "reason": reason,
            "required_manifest_contract": {
                "schema_version": REPLAY_CAPABILITY_SCHEMA_VERSION,
                "protocol": REPLAY_CAPABILITY_PROTOCOL_VERSION,
                "handles_values": list(REPLAY_CAPABILITY_SUPPORTED_REQUIREMENT_KINDS),
                "entrypoint_role": (
                    "relative compiler entrypoint that writes output/result.json"
                ),
                "runtime_files_role": (
                    "candidate-owned files available to result service runtime_entrypoint"
                ),
            },
            "required_compile_result_contract": {
                "schema_version": REPLAY_CAPABILITY_RESULT_SCHEMA_VERSION,
                "capability_identity": (
                    "copy request.capability_id exactly into result.capability_id"
                ),
                "service_transport_values": list(
                    REPLAY_CAPABILITY_SUPPORTED_SERVICE_TRANSPORTS
                ),
                "service_readiness_contract": (
                    "every services[*] item must emit readiness.kind; the requirement "
                    "applies to every wildcard-selected service, not only skill_runtime "
                    "or runtime_required branches"
                ),
                "service_readiness_kind_values": list(
                    REPLAY_CAPABILITY_SUPPORTED_READINESS_KINDS
                ),
                "runtime_service_transport": "skill_runtime",
                "requirement_classification": (
                    "classify every request requirement_id exactly once as handled or "
                    "unhandled"
                ),
            },
            "layering_rules": [
                "manifest protocol is always the subprocess compiler protocol, never a service transport",
                "manifest handles contains request requirement kinds, never readiness states or service transports",
                "runtime_required is a requirement status and must not appear in handles",
                "skill_runtime is a compile-result service transport and must not appear as manifest protocol or handles",
            ],
        }
        if isinstance(exc, ReplayCapabilityError):
            if exc.code:
                diagnostic["capability_error_code"] = exc.code
            diagnostic.update(exc.details)
        details: dict[str, object] = {
            "failure_class": "candidate",
            "failure_owner": FailureOwner.CANDIDATE.value,
            "failure_scope": FailureScope.CANDIDATE.value,
            "failure_source": FailureEventSource.NATIVE.value,
            "repairable": True,
            "diagnostics": [diagnostic],
        }
        if isinstance(exc, ReplayCapabilityError):
            if exc.code:
                details["capability_error_code"] = exc.code
            details.update(exc.details)
        failure_event = ReplayFailureEvent(
            code=(
                exc.code
                if isinstance(exc, ReplayCapabilityError) and exc.code
                else "invalid_replay_capability_compile"
            ),
            owner=FailureOwner.CANDIDATE,
            stage=FailureStage.CAPABILITY_COMPILE,
            scope=FailureScope.CANDIDATE,
            repairable=True,
            category="replay_capability",
            summary=reason,
            contract_fingerprint=(
                schema_field_contract_fingerprint(details)
                if schema_field_contract_fingerprint is not None
                else None
            ),
        )
        details["failure_event"] = failure_event.to_dict()
        details["causal_failure_events"] = [failure_event.to_dict()]
        return details
    return {
        "failure_class": "infrastructure",
        "failure_owner": FailureOwner.INFRASTRUCTURE.value,
        "failure_scope": FailureScope.SHARED_RUN.value,
        "failure_source": FailureEventSource.NATIVE.value,
        "repairable": False,
        "code": "replay_adaptation_infrastructure_error",
    }

def _replay_adaptation_details(
    bundle: ReplayAdaptationBundle,
    *,
    readiness: str,
    artifact_root: Path,
) -> dict[str, object]:
    details: dict[str, object] = {
        "schema_version": bundle.schema_version,
        "readiness": readiness,
        "ready": bundle.ready,
        "adaptation_fingerprint": bundle.adaptation_fingerprint,
        "workspace_seed_fingerprint": bundle.workspace_seed_fingerprint,
        "environment_fingerprint": bundle.environment_fingerprint,
        "bundle_path": str(artifact_root / "bundle.json"),
        "manifest_path": bundle.manifest_path,
        "environment_snapshot_path": bundle.environment_snapshot_path,
        "cases": [
            {
                "case_id": case.case_id,
                "readiness": case.readiness,
                "task_input_fingerprint": case.task_input_fingerprint,
                "dependencies": [
                    {
                        "kind": dependency.kind,
                        "identifier": dependency.identifier,
                        "status": dependency.status,
                        "deterministic": dependency.deterministic,
                        "adapter_id": dependency.adapter_id,
                        "detail": dependency.detail,
                    }
                    for dependency in case.dependencies
                ],
                "tool_names": list(case.tool_names),
                "diagnostics": list(case.diagnostics),
            }
            for case in bundle.cases
        ],
    }
    if bundle.replay_capability is not None:
        capability = bundle.replay_capability
        details["replay_capability"] = {
            "source": "candidate",
            "capability_id": capability.capability_id,
            "capability_package_fingerprint": capability.capability_package_fingerprint,
            "frozen_capability_fingerprint": capability.fingerprint,
            "ready": capability.ready,
            "handled_requirements": list(capability.handled_requirements),
            "unhandled_requirements": list(capability.unhandled_requirements),
        }
    return details
