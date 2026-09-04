"""Typed candidate capability validation outside the Runner facade."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aworld.self_evolve.capability_contracts import validate_applicable_capabilities
from aworld.self_evolve.candidate_package import candidate_package_fingerprint
from aworld.self_evolve.controllers.run_replay_adaptation import (
    ReplayAdaptationExecution,
    ReplayAdaptationRequest,
    execute_replay_adaptation,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.failure_events import (
    FailureEventSource,
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureEvent,
)
from aworld.self_evolve.replay import (
    _replay_service_start_failure_details,
    preflight_frozen_replay_capability,
)
from aworld.self_evolve.replay_adaptation import ReplayCapabilityRequirement
from aworld.self_evolve.sanitization import sanitize_path_ref, sanitize_text
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.target_package import _safe_artifact_name
from aworld.self_evolve.targets import SelfEvolveTarget
from aworld.self_evolve.types import CandidateVariant, GateResult


@dataclass(frozen=True)
class CapabilityValidationRequest:
    run_id: str
    target: SelfEvolveTarget
    dataset: SelfEvolveDataset
    candidate: CandidateVariant
    requirements: tuple[ReplayCapabilityRequirement, ...]


@dataclass(frozen=True)
class CapabilityValidationPolicy:
    replay_enabled: bool


@dataclass(frozen=True)
class CapabilityValidationRuntime:
    store: FilesystemSelfEvolveStore
    replay_adaptation: ReplayAdaptationExecution
    create_candidate_skill_overlay: Callable[..., Any]
    validate_applicable_capabilities: Callable[..., Any] = (
        validate_applicable_capabilities
    )
    preflight_frozen_replay_capability: Callable[..., Any] = (
        preflight_frozen_replay_capability
    )
    replay_service_start_failure_details: Callable[..., Mapping[str, object]] = (
        _replay_service_start_failure_details
    )


@dataclass(frozen=True)
class CapabilityValidationResult:
    gates: tuple[GateResult, ...]

    def as_list(self) -> list[GateResult]:
        return list(self.gates)


def _persistent_preflight_diagnostic_refs(
    artifact_dir: Path,
    *,
    workspace_root: Path,
) -> tuple[str, ...]:
    """Return concrete retained files instead of an ephemeral directory ref."""

    retained_names = {"launch.json", "stdout.txt", "stderr.txt"}
    paths = sorted(
        path
        for path in artifact_dir.rglob("*")
        if path.is_file() and path.name in retained_names
    )
    refs: list[str] = []
    for path in paths[:16]:
        ref = (
            path.relative_to(workspace_root).as_posix()
            if path.is_relative_to(workspace_root)
            else path.name
        )
        refs.append(sanitize_path_ref(ref))
    if refs:
        return tuple(refs)
    ref = (
        artifact_dir.relative_to(workspace_root).as_posix()
        if artifact_dir.is_relative_to(workspace_root)
        else artifact_dir.name
    )
    return (sanitize_path_ref(ref),)


async def validate_candidate_capabilities(
    request: CapabilityValidationRequest,
    policy: CapabilityValidationPolicy,
    runtime: CapabilityValidationRuntime,
) -> CapabilityValidationResult:
    if (
        not policy.replay_enabled
        or not request.requirements
        or request.target.identity.path is None
    ):
        return CapabilityValidationResult(())
    framework_result = execute_replay_adaptation(
        ReplayAdaptationRequest(
            run_id=request.run_id,
            dataset=request.dataset,
            emit_progress=False,
        ),
        runtime.replay_adaptation,
    )
    framework_adaptation, framework_gate = framework_result.as_tuple()
    if framework_gate.passed and framework_adaptation is not None:
        return CapabilityValidationResult(())
    overlay = runtime.create_candidate_skill_overlay(
        workspace_root=runtime.store.workspace_root,
        run_id=request.run_id,
        candidate=request.candidate,
        target_skill_path=request.target.identity.path,
        baseline_skill_roots=getattr(request.target, "baseline_skill_roots", ()),
    )
    results = runtime.validate_applicable_capabilities(
        requirements=request.requirements,
        candidate=request.candidate,
        skill_root=overlay.candidate_skill_path.parent,
    )
    gates: list[GateResult] = []
    for result in results:
        diagnostics = [item.to_dict() for item in result.diagnostics]
        diagnostic_events = [
            event
            for item in diagnostics
            for event in (
                item.get("failure_event"),
                *(item.get("causal_failure_events") or ()),
            )
            if isinstance(event, Mapping)
        ]
        gates.append(
            GateResult(
                gate_name=f"candidate_capability_{result.capability_type}",
                passed=result.passed,
                reason=(
                    "candidate package satisfies registered capability contract"
                    if result.passed
                    else "candidate package violates registered capability contract"
                ),
                details={
                    "capability_type": result.capability_type,
                    "code": diagnostics[0].get("code") if diagnostics else None,
                    "failure_class": (
                        diagnostics[0]["failure_class"] if diagnostics else None
                    ),
                    "repairable": (
                        all(bool(item.get("repairable")) for item in diagnostics)
                        if diagnostics
                        else False
                    ),
                    "diagnostics": diagnostics,
                    **(
                        {
                            "failure_event": dict(diagnostic_events[0]),
                            "causal_failure_events": [
                                dict(event) for event in diagnostic_events
                            ],
                        }
                        if diagnostic_events
                        else {}
                    ),
                },
            )
        )
    if any(not gate.passed for gate in gates):
        return CapabilityValidationResult(tuple(gates))
    replay_gate_index = next(
        (
            index
            for index, gate in enumerate(gates)
            if gate.gate_name == "candidate_capability_replay"
        ),
        None,
    )
    if replay_gate_index is None:
        return CapabilityValidationResult(tuple(gates))
    adaptation_result = execute_replay_adaptation(
        ReplayAdaptationRequest(
            run_id=request.run_id,
            dataset=request.dataset,
            capability_skill_root=overlay.candidate_skill_path.parent,
            candidate_package_fingerprint=candidate_package_fingerprint(
                request.candidate
            ),
            emit_progress=False,
        ),
        runtime.replay_adaptation,
    )
    adaptation, adaptation_gate = adaptation_result.as_tuple()
    if adaptation is None or not adaptation_gate.passed:
        details = dict(adaptation_gate.details or {})
        proven_shared = bool(
            details.get("failure_owner")
            in {FailureOwner.INFRASTRUCTURE.value, FailureOwner.FRAMEWORK.value}
            and details.get("failure_scope") == FailureScope.SHARED_RUN.value
            and details.get("failure_source") == FailureEventSource.NATIVE.value
        )
        owner = FailureOwner.INFRASTRUCTURE if proven_shared else FailureOwner.CANDIDATE
        event = ReplayFailureEvent(
            code=str(
                details.get("capability_error_code")
                or details.get("code")
                or "candidate_capability_compile_failed"
            ),
            owner=owner,
            stage=FailureStage.CAPABILITY_COMPILE,
            scope=(
                FailureScope.SHARED_RUN if proven_shared else FailureScope.CANDIDATE
            ),
            repairable=not proven_shared,
            category="candidate_capability_preflight",
            summary=adaptation_gate.reason,
            diagnostics={
                "gate_name": adaptation_gate.gate_name,
                "candidate_id": request.candidate.candidate_id,
            },
        )
        gates[replay_gate_index] = GateResult(
            gate_name="candidate_capability_replay",
            passed=False,
            reason=(
                "candidate replay capability could not be compiled for operational "
                "preflight"
            ),
            details={
                **details,
                "failure_class": "infrastructure" if proven_shared else "candidate",
                "repairable": not proven_shared,
                "stage": "capability_compile",
                "code": "candidate_capability_compile_failed",
                "failure_event": event.to_dict(),
                "causal_failure_events": [event.to_dict()],
            },
        )
        return CapabilityValidationResult(tuple(gates))
    capability = adaptation.replay_capability
    if capability is None:
        event = ReplayFailureEvent(
            code="candidate_replay_capability_missing_after_compile",
            owner=FailureOwner.CANDIDATE,
            stage=FailureStage.CAPABILITY_COMPILE,
            scope=FailureScope.CANDIDATE,
            repairable=True,
            category="candidate_capability_preflight",
            summary="candidate replay adaptation did not freeze a capability",
        )
        gates[replay_gate_index] = GateResult(
            gate_name="candidate_capability_replay",
            passed=False,
            reason="candidate replay capability was not frozen",
            details={
                "failure_class": "candidate",
                "repairable": True,
                "stage": "capability_compile",
                "code": event.code,
                "failure_event": event.to_dict(),
                "causal_failure_events": [event.to_dict()],
            },
        )
        return CapabilityValidationResult(tuple(gates))
    artifact_dir = (
        runtime.store.run_path(request.run_id)
        / "capability_preflight"
        / _safe_artifact_name(request.candidate.candidate_id)
    )
    try:
        await runtime.preflight_frozen_replay_capability(
            capability, artifact_dir=artifact_dir
        )
    except Exception as exc:
        failure_details = runtime.replay_service_start_failure_details(
            exc, replay_capability=capability
        )
        candidate_owned = failure_details.get("outcome") == "candidate_failure"
        repairable = failure_details.get("repairable") is True
        diagnostic_details = dict(
            failure_details.get("diagnostics")
            if isinstance(failure_details.get("diagnostics"), Mapping)
            else {}
        )
        error_code = str(
            failure_details.get("code")
            or "candidate_capability_operational_preflight_failed"
        )
        owner = (
            FailureOwner.CANDIDATE if candidate_owned else FailureOwner.INFRASTRUCTURE
        )
        diagnostic_refs = _persistent_preflight_diagnostic_refs(
            artifact_dir,
            workspace_root=runtime.store.workspace_root,
        )
        event = ReplayFailureEvent(
            code=error_code,
            owner=owner,
            stage=FailureStage.CAPABILITY_PREFLIGHT,
            scope=(
                FailureScope.CANDIDATE if candidate_owned else FailureScope.SHARED_RUN
            ),
            repairable=repairable,
            category="candidate_capability_preflight",
            summary="candidate replay capability failed operational preflight",
            diagnostics={
                "error_type": type(exc).__name__,
                "reason": sanitize_text(str(exc), max_chars=512),
                **diagnostic_details,
            },
            artifact_refs=diagnostic_refs,
            capability_id=capability.capability_id,
        )
        gates[replay_gate_index] = GateResult(
            gate_name="candidate_capability_replay",
            passed=False,
            reason="candidate replay capability failed operational preflight",
            details={
                "capability_type": "replay",
                "failure_class": ("candidate" if candidate_owned else "infrastructure"),
                "repairable": repairable,
                "stage": "capability_preflight",
                "code": error_code,
                "error_type": type(exc).__name__,
                "artifact_root": str(artifact_dir),
                "diagnostic_refs": list(diagnostic_refs),
                **diagnostic_details,
                "failure_event": event.to_dict(),
                "causal_failure_events": [event.to_dict()],
            },
        )
        return CapabilityValidationResult(tuple(gates))
    gates[replay_gate_index] = GateResult(
        gate_name="candidate_capability_replay",
        passed=True,
        reason=(
            "candidate package satisfies the replay capability contract and "
            "operational preflight"
        ),
        details={
            "capability_type": "replay",
            "failure_class": None,
            "repairable": False,
            "diagnostics": [],
            "operational_preflight": True,
            "capability_id": capability.capability_id,
            "frozen_capability_fingerprint": capability.fingerprint,
            "artifact_root": str(artifact_dir),
        },
    )
    return CapabilityValidationResult(tuple(gates))




__all__ = [
    "CapabilityValidationPolicy",
    "CapabilityValidationRequest",
    "CapabilityValidationResult",
    "CapabilityValidationRuntime",
    "validate_candidate_capabilities",
]
