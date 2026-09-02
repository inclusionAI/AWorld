"""Typed replay-adaptation lifecycle owned outside the Runner facade."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.failure_events import (
    FailureEventSource,
    FailureOwner,
    FailureScope,
)
from aworld.self_evolve.gates import ReplayAdaptationGate
from aworld.self_evolve.replay import (
    replay_dataset_fingerprint,
    replay_support_fingerprint,
    replay_timeout_envelope_fingerprint,
)
from aworld.self_evolve.replay_adaptation import (
    ReplayAdaptationBundle,
    ReplayAdaptationCompiler,
    ReplayPreflightReport,
)
from aworld.self_evolve.replay_adaptation_diagnostics import (
    _replay_adaptation_details,
    _replay_adaptation_exception_details,
)
from aworld.self_evolve.replay_gates import _environment_fingerprint_drift_gate
from aworld.self_evolve.replay_capability import (
    FrozenReplayCapabilityAdapter,
    ReplayCapabilityCompileRequest,
    compile_and_freeze_capability,
    discover_replay_capability,
    materialize_replay_evidence_derivations,
)
from aworld.self_evolve.sanitization import sanitize_text
from aworld.self_evolve.schema_diagnostics import _schema_field_contract_fingerprint
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.target_package import (
    _replayable_user_task_dataset,
    _safe_artifact_name,
    _stable_json_fingerprint,
)
from aworld.self_evolve.targets import SelfEvolveTarget
from aworld.self_evolve.types import GateResult


@dataclass(frozen=True)
class ReplayAdaptationRequest:
    run_id: str
    dataset: SelfEvolveDataset
    capability_skill_root: str | Path | None = None
    candidate_package_fingerprint: str | None = None
    emit_progress: bool = True


@dataclass(frozen=True)
class ReplayAdaptationRuntime:
    store: FilesystemSelfEvolveStore
    compiler: ReplayAdaptationCompiler
    progress_callback: Callable[[str, str], Any] | None = None
    emit_progress: (
        Callable[[Callable[[str, str], Any] | None, str, str], None] | None
    ) = None
    schema_field_contract_fingerprint: Callable[[object], str | None] = (
        _schema_field_contract_fingerprint
    )


@dataclass
class ReplayAdaptationState:
    """Mutable caches with explicit run ownership and legacy mapping aliases."""

    adaptation_cache: dict[
        tuple[str, str, str], tuple[ReplayAdaptationBundle | None, GateResult]
    ] = field(default_factory=dict)
    dataset_preflight_cache: dict[str, ReplayPreflightReport] = field(
        default_factory=dict
    )
    environment_fingerprints: dict[str, str] = field(default_factory=dict)

    def cleanup_run(self, run_id: str) -> None:
        self.environment_fingerprints.pop(run_id, None)
        stale = [key for key in self.adaptation_cache if key[0] == run_id]
        for key in stale:
            self.adaptation_cache.pop(key, None)


@dataclass(frozen=True)
class ReplayAdaptationResult:
    bundle: ReplayAdaptationBundle | None
    gate: GateResult

    def as_tuple(self) -> tuple[ReplayAdaptationBundle | None, GateResult]:
        return self.bundle, self.gate


class ReplayAdaptationOverride(Protocol):
    """Typed outer-adapter seam for legacy Runner monkeypatches."""

    def __call__(self, request: ReplayAdaptationRequest) -> ReplayAdaptationResult: ...


@dataclass(frozen=True)
class ReplayAdaptationExecution:
    """Nested typed composition shared by replay-dependent controllers."""

    runtime: ReplayAdaptationRuntime
    state: ReplayAdaptationState
    override: ReplayAdaptationOverride | None = None

    def execute(self, request: ReplayAdaptationRequest) -> ReplayAdaptationResult:
        if self.override is not None:
            return self.override(request)
        return prepare_replay_adaptation(request, self.runtime, self.state)


def execute_replay_adaptation(
    request: ReplayAdaptationRequest,
    execution: ReplayAdaptationExecution,
) -> ReplayAdaptationResult:
    return execution.execute(request)


@dataclass(frozen=True)
class BaselineReuseProvenanceRequest:
    run_id: str
    target: SelfEvolveTarget
    dataset: SelfEvolveDataset
    replay_adaptation: ReplayAdaptationBundle | None = None
    timeout_seconds: float | None = None
    max_steps: int | None = None
    max_tool_calls: int | None = None


@dataclass(frozen=True)
class BaselineReuseProvenanceRuntime:
    replay_adaptation: ReplayAdaptationExecution


@dataclass(frozen=True)
class BaselineReuseProvenanceResult:
    provenance: dict[str, str | None]














def prepare_replay_adaptation(
    request: ReplayAdaptationRequest,
    runtime: ReplayAdaptationRuntime,
    state: ReplayAdaptationState,
) -> ReplayAdaptationResult:
    dataset_fingerprint = replay_dataset_fingerprint(request.dataset)
    requested_package_fingerprint = (
        request.candidate_package_fingerprint or "framework-only"
    )
    capability = None
    discovery_error: Exception | None = None
    try:
        capability = (
            discover_replay_capability(request.capability_skill_root)
            if request.capability_skill_root is not None
            else None
        )
    except Exception as exc:
        discovery_error = exc
    discovered_package_fingerprint = (
        capability.package_fingerprint if capability is not None else "none"
    )
    if discovery_error is not None:
        capability_cache_key = (
            f"candidate-discovery-error:{requested_package_fingerprint}"
        )
    elif capability is not None:
        capability_cache_key = f"replay-capability:{discovered_package_fingerprint}"
    elif request.capability_skill_root is not None:
        capability_cache_key = "candidate-without-replay-capability"
    else:
        capability_cache_key = "framework-only"
    cache_key = (request.run_id, dataset_fingerprint, capability_cache_key)
    cached = state.adaptation_cache.get(cache_key)
    if cached is not None:
        return ReplayAdaptationResult(*cached)
    if request.emit_progress and runtime.emit_progress is not None:
        runtime.emit_progress(
            runtime.progress_callback,
            "replay_adaptation",
            "Compiling replay paths, workspace seed, and dependency bindings",
        )
    replayable_dataset = _replayable_user_task_dataset(request.dataset)
    artifact_root = (
        runtime.store.run_path(request.run_id)
        / "replay_adaptation"
        / dataset_fingerprint.removeprefix("sha256:")[:16]
        / hashlib.sha256(capability_cache_key.encode("utf-8")).hexdigest()[:16]
    )
    try:
        if discovery_error is not None:
            raise discovery_error
        preflight_cache_key = replay_dataset_fingerprint(replayable_dataset)
        preflight = state.dataset_preflight_cache.get(preflight_cache_key)
        preflight_cache_hit = preflight is not None
        if preflight is None:
            preflight = runtime.compiler.preflight(
                dataset=replayable_dataset,
                workspace_root=runtime.store.workspace_root,
            )
            state.dataset_preflight_cache[preflight_cache_key] = preflight
        if (
            preflight.requirements
            and capability is None
            and not runtime.compiler.adapters
        ):
            result = ReplayAdaptationResult(
                None,
                GateResult(
                    gate_name="replay_capability",
                    passed=False,
                    reason=(
                        "replay requirements exist but the selected skill candidate "
                        "does not provide a skill-owned replay capability"
                    ),
                    details={
                        "failure_class": (
                            "candidate"
                            if request.capability_skill_root is not None
                            else "infrastructure"
                        ),
                        "failure_owner": (
                            FailureOwner.CANDIDATE.value
                            if request.capability_skill_root is not None
                            else FailureOwner.INFRASTRUCTURE.value
                        ),
                        "failure_scope": (
                            FailureScope.CANDIDATE.value
                            if request.capability_skill_root is not None
                            else FailureScope.SHARED_RUN.value
                        ),
                        "failure_source": FailureEventSource.NATIVE.value,
                        "repairable": request.capability_skill_root is not None,
                        "code": "candidate_replay_capability_missing",
                        "requirement_count": len(preflight.requirements),
                        "requirement_kinds": sorted(
                            {item.kind for item in preflight.requirements}
                        ),
                        "preflight_fingerprint": preflight.fingerprint,
                        "preflight_cache_hit": preflight_cache_hit,
                        "artifact_root": str(artifact_root),
                    },
                ),
            )
            state.adaptation_cache[cache_key] = result.as_tuple()
            return result
        frozen_capability = None
        additional_adapters = ()
        if capability is not None and preflight.requirements:
            context_root = artifact_root / "trajectory_context"
            context_root.mkdir(parents=True, exist_ok=True)
            context_snapshots: dict[str, str] = {}
            context_fingerprints: list[str] = []
            for case in replayable_dataset.cases:
                if case.context_snapshot is None:
                    continue
                snapshot_path = (
                    context_root / f"{_safe_artifact_name(case.case_id)}.json"
                )
                snapshot_path.write_text(
                    json.dumps(
                        asdict(case.context_snapshot),
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                context_snapshots[case.case_id] = str(snapshot_path)
                context_fingerprints.append(case.context_snapshot.fingerprint)
            context_fingerprint = _stable_json_fingerprint(
                {
                    "dataset_fingerprint": dataset_fingerprint,
                    "context_fingerprints": sorted(context_fingerprints),
                    "preflight_fingerprint": preflight.fingerprint,
                }
            )
            task_inputs = {
                case.case_id: case.input for case in replayable_dataset.cases
            }
            compile_request = ReplayCapabilityCompileRequest.create(
                requirements=preflight.requirements,
                context_snapshots=context_snapshots,
                task_inputs=task_inputs,
                capability_root=capability.skill_root,
                capability_package_fingerprint=capability.package_fingerprint,
                context_fingerprint=context_fingerprint,
            )
            evidence_derivations = materialize_replay_evidence_derivations(
                compile_request,
                context_root / "evidence_derivations",
            )
            compile_request = ReplayCapabilityCompileRequest.create(
                requirements=preflight.requirements,
                context_snapshots=context_snapshots,
                task_inputs=task_inputs,
                capability_root=capability.skill_root,
                capability_package_fingerprint=capability.package_fingerprint,
                context_fingerprint=context_fingerprint,
                evidence_derivations=evidence_derivations,
            )
            frozen_capability = compile_and_freeze_capability(
                capability,
                compile_request,
                artifact_root / "skill_replay_capability",
            )
            additional_adapters = (
                FrozenReplayCapabilityAdapter(
                    capability=frozen_capability,
                    requirements=preflight.requirements,
                ),
            )
        bundle = runtime.compiler.compile(
            dataset=replayable_dataset,
            workspace_root=runtime.store.workspace_root,
            artifact_root=artifact_root,
            additional_adapters=additional_adapters,
            replay_capability=frozen_capability,
        )
        expected_environment_fingerprint = state.environment_fingerprints.get(
            request.run_id
        )
        if expected_environment_fingerprint is None:
            state.environment_fingerprints[request.run_id] = (
                bundle.environment_fingerprint
            )
        else:
            environment_drift_gate = _environment_fingerprint_drift_gate(
                expected_environment_fingerprint,
                bundle.environment_fingerprint,
            )
            if environment_drift_gate is not None:
                result = ReplayAdaptationResult(None, environment_drift_gate)
                state.adaptation_cache[cache_key] = result.as_tuple()
                return result
    except Exception as exc:
        failure_details = _replay_adaptation_exception_details(
            exc,
            candidate_capability=request.capability_skill_root is not None,
            schema_field_contract_fingerprint=(
                runtime.schema_field_contract_fingerprint
            ),
        )
        result = ReplayAdaptationResult(
            None,
            GateResult(
                gate_name="replay_adaptation",
                passed=False,
                reason="replay adaptation compilation failed",
                details={
                    **failure_details,
                    "type": type(exc).__name__,
                    "reason": sanitize_text(str(exc), max_chars=240),
                    "artifact_root": str(artifact_root),
                },
            ),
        )
        state.adaptation_cache[cache_key] = result.as_tuple()
        return result
    base_gate = ReplayAdaptationGate().evaluate(bundle)
    readiness = str((base_gate.details or {}).get("readiness") or "unresolved")
    gate = replace(
        base_gate,
        details={
            **dict(base_gate.details or {}),
            **(
                {
                    "failure_class": "candidate",
                    "failure_owner": FailureOwner.CANDIDATE.value,
                    "failure_scope": FailureScope.CANDIDATE.value,
                    "failure_source": FailureEventSource.NATIVE.value,
                    "repairable": True,
                }
                if request.capability_skill_root is not None and not base_gate.passed
                else {}
            ),
            **_replay_adaptation_details(
                bundle,
                readiness=readiness,
                artifact_root=artifact_root,
            ),
            "preflight_cache_hit": preflight_cache_hit,
        },
    )
    result = ReplayAdaptationResult(bundle, gate)
    state.adaptation_cache[cache_key] = result.as_tuple()
    return result


_EMPTY_BASELINE_PROVENANCE: dict[str, str | None] = {
    "baseline_skill_fingerprint": None,
    "dataset_fingerprint": None,
    "adaptation_fingerprint": None,
    "workspace_seed_fingerprint": None,
    "support_fingerprint": None,
    "timeout_envelope_fingerprint": None,
}


def baseline_reuse_provenance(
    request: BaselineReuseProvenanceRequest,
    runtime: BaselineReuseProvenanceRuntime,
) -> BaselineReuseProvenanceResult:
    bundle = request.replay_adaptation
    gate: GateResult | None = None
    if bundle is None:
        adaptation = execute_replay_adaptation(
            ReplayAdaptationRequest(
                run_id=request.run_id,
                dataset=request.dataset,
                emit_progress=False,
            ),
            runtime.replay_adaptation,
        )
        bundle, gate = adaptation.as_tuple()
    if bundle is None or (gate is not None and not gate.passed):
        return BaselineReuseProvenanceResult(dict(_EMPTY_BASELINE_PROVENANCE))
    if not isinstance(bundle, ReplayAdaptationBundle):
        return BaselineReuseProvenanceResult(dict(_EMPTY_BASELINE_PROVENANCE))
    return BaselineReuseProvenanceResult(
        {
            "baseline_skill_fingerprint": request.target.fingerprint_current_content(),
            "dataset_fingerprint": replay_dataset_fingerprint(request.dataset),
            "adaptation_fingerprint": bundle.adaptation_fingerprint,
            "workspace_seed_fingerprint": bundle.workspace_seed_fingerprint,
            "support_fingerprint": replay_support_fingerprint(bundle),
            "timeout_envelope_fingerprint": (
                replay_timeout_envelope_fingerprint(
                    timeout_seconds=request.timeout_seconds,
                    max_steps=request.max_steps,
                    max_tool_calls=request.max_tool_calls,
                )
                if request.timeout_seconds is not None
                else None
            ),
        }
    )


__all__ = [
    "BaselineReuseProvenanceRequest",
    "BaselineReuseProvenanceResult",
    "BaselineReuseProvenanceRuntime",
    "ReplayAdaptationRequest",
    "ReplayAdaptationResult",
    "ReplayAdaptationRuntime",
    "ReplayAdaptationState",
    "ReplayAdaptationExecution",
    "ReplayAdaptationOverride",
    "baseline_reuse_provenance",
    "execute_replay_adaptation",
    "prepare_replay_adaptation",
]
