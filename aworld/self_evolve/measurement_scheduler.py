"""Bounded pair-lane scheduling for trusted self-evolve measurements.

The scheduler is deliberately infrastructure-agnostic.  It owns admission and
pair ordering, while replay owns how one control or treatment observation is
executed and persisted.  Concurrency is enabled only by a canonical
``IsolationDecision``; callers cannot promote an integer or a fingerprint into
proof of isolation.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import os
import sys
import time
from collections import deque
from dataclasses import InitVar, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable, Generic, Mapping, Sequence, TypeVar

from aworld.core.tool.replay_policy import EvidencePolicyProfileV2
from aworld.core.tool.replay_policy import issue_framework_evidence_writer_attestation_v2
from aworld.self_evolve.measurement_control import (
    LaneMaterializationAttestationV1,
    LaneMaterializationClaim,
    MeasurementArm,
    MeasurementControlObservationRecord,
    MeasurementPlanV2,
    MeasurementWorkUnitState,
    stable_control_fingerprint,
)
from aworld.self_evolve.replay_adaptation import (
    IsolationDecision,
    IsolationGrant,
    ReplayIsolationTopology,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore


ControlT = TypeVar("ControlT")
TreatmentT = TypeVar("TreatmentT")
_MEASUREMENT_SCHEDULE_BUNDLE_SEAL = object()
_LANE_MATERIALIZATION_RESULT_SEAL = object()


class PairLaneStopKind(str, Enum):
    COMPLETED = "completed"
    DECISIVE_STOP = "decisive_stop"
    CHECKPOINT_QUANTUM = "checkpoint_quantum"
    CAMPAIGN_DEADLINE = "campaign_deadline"


@dataclass(frozen=True)
class PairLaneWorkItem:
    case_id: str
    repetition_id: int
    stage_id: str
    control_work_unit_id: str
    treatment_work_unit_id: str
    priority: float = 0.0
    control_reused: bool = False

    def __post_init__(self) -> None:
        for value, label in (
            (self.case_id, "case_id"),
            (self.stage_id, "stage_id"),
            (self.control_work_unit_id, "control_work_unit_id"),
            (self.treatment_work_unit_id, "treatment_work_unit_id"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be non-empty")
        if (
            isinstance(self.repetition_id, bool)
            or not isinstance(self.repetition_id, int)
            or self.repetition_id <= 0
        ):
            raise ValueError("repetition_id must be positive")
        if (
            isinstance(self.priority, bool)
            or not isinstance(self.priority, (int, float))
            or not math.isfinite(float(self.priority))
        ):
            raise ValueError("priority must be finite")
        if self.control_work_unit_id == self.treatment_work_unit_id:
            raise ValueError("control and treatment work units must differ")
        if not isinstance(self.control_reused, bool):
            raise ValueError("control_reused must be boolean")

    @property
    def coordinate(self) -> tuple[str, int]:
        return (self.case_id, self.repetition_id)


@dataclass(frozen=True)
class PairLaneResult(Generic[ControlT, TreatmentT]):
    item: PairLaneWorkItem
    context: "LaneExecutionContext"
    control: ControlT
    treatment: TreatmentT | None
    treatment_admitted: bool


@dataclass(frozen=True)
class PairLaneScheduleResult(Generic[ControlT, TreatmentT]):
    stop_kind: PairLaneStopKind
    stop_reason: str
    safe_lane_count: int
    isolation_decision_fingerprint: str
    completed: tuple[PairLaneResult[ControlT, TreatmentT], ...]
    pending: tuple[PairLaneWorkItem, ...]
    elapsed_seconds: float


@dataclass(frozen=True)
class LaneExecutionContext:
    lane_id: int
    measurement_plan_fingerprint: str
    isolation_decision_fingerprint: str
    evidence_policy_fingerprint: str
    grant: IsolationGrant | None
    lane_materialization_fingerprint: str | None = None
    evidence_writer_attestation_fingerprint: str | None = None
    lane_attestation: LaneMaterializationAttestationV1 | None = None


@dataclass(frozen=True)
class LaneMaterializationResult:
    """Framework materializer output before it is admitted as a lane lease.

    This value describes the resources that were actually materialized.  It is
    deliberately not an attestation: the scheduler must compare it with the
    frozen grant and issue the framework-owned writer identity itself.
    """

    topology: ReplayIsolationTopology
    binding_fingerprints: tuple[str, ...] = ()
    claims: tuple[LaneMaterializationClaim, ...] = ()
    _seal: InitVar[object] = None

    def __post_init__(self, _seal: object) -> None:
        if _seal is not _LANE_MATERIALIZATION_RESULT_SEAL:
            raise ValueError(
                "lane materialization result must be framework materializer issued"
            )
        object.__setattr__(
            self,
            "topology",
            ReplayIsolationTopology.from_dict(self.topology.to_dict()),
        )
        bindings = tuple(sorted(self.binding_fingerprints))
        if len(bindings) != len(set(bindings)):
            raise ValueError("materialized lane bindings must be unique")
        object.__setattr__(self, "binding_fingerprints", bindings)
        claims = tuple(sorted(self.claims, key=lambda item: item.dimension))
        if len(claims) != 5:
            raise ValueError("lane materialization result must probe all core claims")
        object.__setattr__(self, "claims", claims)


class FrameworkFilesystemLaneMaterializer:
    """Create and probe framework-owned lane namespaces under one safe root."""

    _DIMENSIONS = (
        ("workspace_root", "workspace_identity"),
        ("runtime_root", "runtime_identity"),
        ("browser_profile", "browser_profile_identity"),
        ("endpoint_namespace", "endpoint_namespace_identity"),
        ("evidence_directory", "evidence_directory_identity"),
    )

    def __init__(self, root: Path, *, _worker_delay_seconds: float = 0.0) -> None:
        absolute = Path(root).absolute()
        absolute.mkdir(mode=0o700, parents=True, exist_ok=True)
        if absolute.is_symlink() or not absolute.is_dir():
            raise ValueError("lane materialization root must be a real directory")
        if any(item.is_symlink() for item in (absolute, *absolute.parents)):
            raise ValueError("lane materialization root has a symlink ancestor")
        self._root = absolute.resolve()
        if (
            isinstance(_worker_delay_seconds, bool)
            or not isinstance(_worker_delay_seconds, (int, float))
            or not math.isfinite(float(_worker_delay_seconds))
            or _worker_delay_seconds < 0
        ):
            raise ValueError("worker delay must be a finite non-negative number")
        self._worker_delay_seconds = float(_worker_delay_seconds)

    async def materialize(
        self,
        context: LaneExecutionContext,
    ) -> LaneMaterializationResult:
        topology, bindings, marker_payload = self._materialization_spec(context)
        expected_paths = self._expected_claim_paths(context)
        preexisting = {
            path: (path.exists(), (path / ".aworld-lane-owner").exists())
            for path in expected_paths
        }
        helper = Path(__file__).with_name("_lane_materializer_helper.py")
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(helper),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        completed = False
        try:
            stdout, stderr = await process.communicate(
                json.dumps(
                    {
                        "root": str(self._root),
                        "delay_seconds": self._worker_delay_seconds,
                        "marker_payload": marker_payload,
                        "claims": [
                            {
                                "dimension": dimension,
                                "declared_identity": str(path),
                            }
                            for (dimension, _field), path in zip(
                                self._DIMENSIONS, expected_paths, strict=True
                            )
                        ],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace")[:2000]
                if detail.startswith("ValueError:"):
                    raise ValueError(detail)
                raise RuntimeError(f"lane materialization helper failed: {detail}")
            payload = json.loads(stdout)
            result = LaneMaterializationResult(
                topology=topology,
                binding_fingerprints=bindings,
                claims=tuple(
                    LaneMaterializationClaim.from_dict(_mapping(item, "claim"))
                    for item in _sequence(payload.get("claims"), "claims")
                ),
                _seal=_LANE_MATERIALIZATION_RESULT_SEAL,
            )
            completed = True
            return result
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            if not completed:
                self._cleanup_new_claim_paths(
                    expected_paths=expected_paths,
                    preexisting=preexisting,
                )

    def _materialization_spec(
        self, context: LaneExecutionContext
    ) -> tuple[ReplayIsolationTopology, tuple[str, ...], str]:
        grant = context.grant
        if grant is None:
            lane_root = self._root / f"exclusive-lane-{context.lane_id}"
            identities = {
                dimension: str(lane_root / dimension)
                for dimension, _field_name in self._DIMENSIONS
            }
            topology = ReplayIsolationTopology.create(
                materializer_id="framework-filesystem-lane-materializer",
                materializer_fingerprint=stable_control_fingerprint(
                    {
                        "schema_version": "aworld.framework_lane_materializer.v1",
                        "kind": "filesystem",
                    }
                ),
                workspace_identity=identities["workspace_root"],
                runtime_identity=identities["runtime_root"],
                browser_profile_identity=identities["browser_profile"],
                endpoint_namespace_identity=identities["endpoint_namespace"],
                evidence_directory_identity=identities["evidence_directory"],
                cleanup_owner=f"framework-lane-{context.lane_id}",
            )
            bindings: tuple[str, ...] = ()
        else:
            if grant.services or grant.resources:
                raise ValueError(
                    "filesystem lane materializer cannot prove service/resource leases"
                )
            topology = ReplayIsolationTopology.create(
                materializer_id=grant.materializer_id,
                materializer_fingerprint=grant.materializer_fingerprint,
                workspace_identity=grant.workspace_identity,
                runtime_identity=grant.runtime_identity,
                browser_profile_identity=grant.browser_profile_identity,
                endpoint_namespace_identity=grant.endpoint_namespace_identity,
                evidence_directory_identity=grant.evidence_directory_identity,
                cleanup_owner=grant.cleanup_owner,
            )
            bindings = grant.binding_fingerprints
        marker_payload = stable_control_fingerprint(
            {
                "measurement_plan_fingerprint": context.measurement_plan_fingerprint,
                "isolation_decision_fingerprint": context.isolation_decision_fingerprint,
                "lane_id": context.lane_id,
                "cleanup_owner": topology.cleanup_owner,
            }
        )
        return topology, bindings, marker_payload

    def _expected_claim_paths(
        self, context: LaneExecutionContext
    ) -> tuple[Path, ...]:
        if context.grant is None:
            lane_root = self._root / f"exclusive-lane-{context.lane_id}"
            return tuple(
                lane_root / dimension for dimension, _field_name in self._DIMENSIONS
            )
        return tuple(
            Path(str(getattr(context.grant, field_name)))
            for _dimension, field_name in self._DIMENSIONS
        )

    def _cleanup_new_claim_paths(
        self,
        *,
        expected_paths: tuple[Path, ...],
        preexisting: Mapping[Path, tuple[bool, bool]],
    ) -> None:
        for path in reversed(expected_paths):
            path_existed, marker_existed = preexisting[path]
            try:
                if path.resolve(strict=False).is_relative_to(self._root):
                    marker = path / ".aworld-lane-owner"
                    if (
                        not marker_existed
                        and marker.is_file()
                        and not marker.is_symlink()
                    ):
                        marker.unlink()
                    if not path_existed:
                        path.rmdir()
            except OSError:
                continue
        for parent in sorted(
            {
                ancestor
                for path in expected_paths
                for ancestor in path.parents
                if ancestor != self._root
                and ancestor.resolve(strict=False).is_relative_to(self._root)
            },
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            try:
                parent.rmdir()
            except OSError:
                continue

    def _materialize_sync(
        self, context: LaneExecutionContext
    ) -> LaneMaterializationResult:
        topology, bindings, marker_payload = self._materialization_spec(context)
        identities = {
            dimension: str(getattr(topology, field_name))
            for dimension, field_name in self._DIMENSIONS
        }
        claims = tuple(
            self._materialize_claim(
                dimension=dimension,
                declared_identity=identities[dimension],
                marker_payload=marker_payload,
            )
            for dimension, _field_name in self._DIMENSIONS
        )
        return LaneMaterializationResult(
            topology=topology,
            binding_fingerprints=bindings,
            claims=claims,
            _seal=_LANE_MATERIALIZATION_RESULT_SEAL,
        )

    def _materialize_claim(
        self,
        *,
        dimension: str,
        declared_identity: str,
        marker_payload: str,
    ) -> LaneMaterializationClaim:
        path = Path(declared_identity)
        if not path.is_absolute():
            raise ValueError(
                f"isolated {dimension} must be an absolute framework-owned path"
            )
        absolute = path.absolute()
        if not absolute.resolve(strict=False).is_relative_to(self._root):
            raise ValueError(f"isolated {dimension} escapes materialization root")
        existing_ancestors = [
            item for item in (absolute, *absolute.parents) if item.exists()
        ]
        if any(item.is_symlink() for item in existing_ancestors):
            raise ValueError(f"isolated {dimension} has a symlink component")
        absolute.mkdir(mode=0o700, parents=True, exist_ok=True)
        if absolute.is_symlink() or not absolute.is_dir():
            raise ValueError(f"isolated {dimension} is not a real directory")
        marker = absolute / ".aworld-lane-owner"
        encoded = (marker_payload + "\n").encode("ascii")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(marker, flags, 0o600)
        except FileExistsError:
            if marker.is_symlink() or marker.read_bytes() != encoded:
                raise ValueError(f"isolated {dimension} ownership marker conflicts")
        else:
            try:
                written = os.write(descriptor, encoded)
                if written != len(encoded):
                    raise OSError("short lane ownership marker write")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        stat = absolute.stat(follow_symlinks=False)
        return LaneMaterializationClaim(
            dimension=dimension,
            declared_identity=str(absolute),
            observed_device=stat.st_dev,
            observed_inode=stat.st_ino,
            ownership_marker_fingerprint=(
                "sha256:" + hashlib.sha256(encoded).hexdigest()
            ),
        )


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def _sequence(value: object, field_name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be an array")
    return value




@dataclass(frozen=True)
class ResolvedControl(Generic[ControlT]):
    content_bytes: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.content_bytes, bytes):
            raise TypeError("resolved control content must be immutable bytes")
        if not self.content_bytes or len(self.content_bytes) > 1_000_000:
            raise ValueError("resolved control content must be bounded and non-empty")
        try:
            value = json.loads(self.content_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("resolved control content must be canonical JSON") from exc
        canonical = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        if canonical != self.content_bytes:
            raise ValueError("resolved control content is not canonical JSON")

    @classmethod
    def from_value(cls, value: ControlT) -> "ResolvedControl[ControlT]":
        return cls(
            content_bytes=json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("utf-8")
        )

    @property
    def value(self) -> ControlT:
        return json.loads(self.content_bytes.decode("utf-8"))

    @property
    def result_fingerprint(self) -> str:
        return "sha256:" + hashlib.sha256(self.content_bytes).hexdigest()


@dataclass(frozen=True)
class MeasurementScheduleBundle:
    """Store-loaded plan plus the only artifacts allowed to authorize execution."""

    plan: MeasurementPlanV2
    isolation_decision: IsolationDecision
    evidence_policy_profile: EvidencePolicyProfileV2
    terminal_observations: tuple[MeasurementControlObservationRecord, ...]
    _seal: InitVar[object] = None

    def __post_init__(self, _seal: object) -> None:
        if _seal is not _MEASUREMENT_SCHEDULE_BUNDLE_SEAL:
            raise ValueError("measurement schedule bundle must be loaded from store")
        verified = MeasurementPlanV2.from_dict(
            self.plan.to_dict(),
            isolation_decision=self.isolation_decision,
            evidence_policy_profile=self.evidence_policy_profile,
        )
        if verified != self.plan:
            raise ValueError("measurement schedule bundle plan is not canonical")
        observations = tuple(self.terminal_observations)
        known = {unit.work_unit_id: unit for unit in self.plan.work_units}
        if len({item.work_unit_id for item in observations}) != len(observations):
            raise ValueError("measurement schedule bundle has duplicate observations")
        for observation in observations:
            unit = known.get(observation.work_unit_id)
            if unit is None:
                raise ValueError("terminal observation is outside the plan")
            if (
                observation.measurement_plan_fingerprint
                != self.plan.measurement_plan_fingerprint
                or observation.experiment_id != unit.experiment_id
                or observation.case_id != unit.case_id
                or observation.arm is not unit.arm
                or observation.repetition_id != unit.repetition_id
            ):
                raise ValueError("terminal observation coordinates do not match plan")
        object.__setattr__(self, "terminal_observations", observations)

    def work_items(
        self,
        *,
        admitted_stage_ids: Sequence[str] | None = None,
        admitted_case_ids: Sequence[str] | None = None,
    ) -> tuple[PairLaneWorkItem, ...]:
        return _pair_lane_work_from_bundle(
            self,
            admitted_stage_ids=admitted_stage_ids,
            admitted_case_ids=admitted_case_ids,
        )

    @property
    def observation_by_work_unit(
        self,
    ) -> dict[str, MeasurementControlObservationRecord]:
        return {item.work_unit_id: item for item in self.terminal_observations}


def load_measurement_schedule_bundle(
    store: FilesystemSelfEvolveStore,
    *,
    run_id: str,
    measurement_plan_fingerprint: str,
) -> MeasurementScheduleBundle:
    plan = store.read_measurement_control_plan(run_id, measurement_plan_fingerprint)
    decision, profile = store.read_measurement_control_contracts(
        run_id, measurement_plan_fingerprint
    )
    index = store.read_measurement_control_index(
        run_id, measurement_plan_fingerprint
    )
    observations = tuple(
        store.read_measurement_control_observation(
            run_id,
            measurement_plan_fingerprint,
            entry.observation_fingerprint,
        )
        for entry in index.work_units
        if entry.state.terminal and entry.observation_fingerprint is not None
    )
    return MeasurementScheduleBundle(
        plan=plan,
        isolation_decision=decision,
        evidence_policy_profile=profile,
        terminal_observations=observations,
        _seal=_MEASUREMENT_SCHEDULE_BUNDLE_SEAL,
    )


PairExecutor = Callable[[PairLaneWorkItem, LaneExecutionContext], Awaitable[ControlT]]
TreatmentExecutor = Callable[
    [PairLaneWorkItem, LaneExecutionContext, ControlT], Awaitable[TreatmentT]
]
ReusedControlResolver = Callable[
    [MeasurementControlObservationRecord, LaneExecutionContext],
    Awaitable[ResolvedControl[ControlT]],
]
ControlAdmission = Callable[[PairLaneWorkItem, ControlT], bool]
StopDecision = Callable[
    [tuple[PairLaneResult[ControlT, TreatmentT], ...]],
    str | None | Awaitable[str | None],
]
ScheduleObserver = Callable[
    [str, Mapping[str, object]], None | Awaitable[None]
]


def _pair_lane_work_from_bundle(
    bundle: MeasurementScheduleBundle,
    *,
    admitted_stage_ids: Sequence[str] | None = None,
    admitted_case_ids: Sequence[str] | None = None,
) -> tuple[PairLaneWorkItem, ...]:
    """Compile pending pairs only from store-verified terminal observations."""

    verified_plan = bundle.plan
    stage_ids = (
        tuple(admitted_stage_ids)
        if admitted_stage_ids is not None
        else tuple(stage.stage_id for stage in verified_plan.stages)
    )
    if len(stage_ids) != len(set(stage_ids)):
        raise ValueError("admitted stage ids must be unique")
    known_stage_ids = {stage.stage_id for stage in verified_plan.stages}
    if any(stage_id not in known_stage_ids for stage_id in stage_ids):
        raise ValueError("admitted stage is outside the frozen plan")
    requested_case_ids = (
        tuple(admitted_case_ids)
        if admitted_case_ids is not None
        else tuple(verified_plan.case_ids)
    )
    case_ids = frozenset(requested_case_ids)
    if len(case_ids) != len(requested_case_ids):
        raise ValueError("admitted case ids must be unique")
    if not case_ids.issubset(set(verified_plan.case_ids)):
        raise ValueError("admitted case is outside the frozen plan")
    observations = bundle.observation_by_work_unit
    terminal_ids = frozenset(observations)
    known_unit_ids = {unit.work_unit_id for unit in verified_plan.work_units}
    if not terminal_ids.issubset(known_unit_ids):
        raise ValueError("terminal work-unit id is outside the frozen plan")
    units = {unit.work_unit_id: unit for unit in verified_plan.work_units}
    stage_priority = {
        stage.stage_id: float(len(verified_plan.stages) - index)
        for index, stage in enumerate(verified_plan.stages)
    }
    work: list[PairLaneWorkItem] = []
    for treatment in verified_plan.work_units:
        if (
            treatment.arm is not MeasurementArm.TREATMENT
            or treatment.stage_id not in stage_ids
            or treatment.case_id not in case_ids
        ):
            continue
        control_id = treatment.depends_on_work_unit_id
        assert control_id is not None
        control = units[control_id]
        treatment_terminal = treatment.work_unit_id in terminal_ids
        control_terminal = control.work_unit_id in terminal_ids
        if treatment_terminal:
            if not control_terminal:
                raise ValueError(
                    "terminal treatment cannot exist without its terminal control"
                )
            continue
        if control_terminal and (
            observations[control.work_unit_id].terminal_state
            is not MeasurementWorkUnitState.SUCCEEDED
        ):
            raise ValueError(
                "non-successful terminal control requires terminal paired treatment"
            )
        work.append(
            PairLaneWorkItem(
                case_id=treatment.case_id,
                repetition_id=treatment.repetition_id,
                stage_id=treatment.stage_id,
                control_work_unit_id=control.work_unit_id,
                treatment_work_unit_id=treatment.work_unit_id,
                priority=stage_priority[treatment.stage_id],
                control_reused=control_terminal,
            )
        )
    return tuple(work)


async def schedule_pair_lanes(
    store: FilesystemSelfEvolveStore,
    *,
    run_id: str,
    measurement_plan_fingerprint: str,
    run_control: PairExecutor[ControlT],
    run_treatment: TreatmentExecutor[ControlT, TreatmentT],
    lane_materializer: FrameworkFilesystemLaneMaterializer,
    resolve_reused_control: ReusedControlResolver[ControlT] | None = None,
    control_allows_treatment: ControlAdmission[ControlT] | None = None,
    should_stop: StopDecision[ControlT, TreatmentT] | None = None,
    observer: ScheduleObserver | None = None,
    checkpoint_quantum_seconds: float | None = None,
    campaign_deadline_monotonic: float | None = None,
    materialization_timeout_seconds: float | None = None,
    configured_lane_limit: int = 2,
    admitted_stage_ids: Sequence[str] | None = None,
    admitted_case_ids: Sequence[str] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> PairLaneScheduleResult[ControlT, TreatmentT]:
    """Execute independent case pairs with bounded, proof-carrying concurrency.

    New pairs are never admitted after a decisive stop or a scheduling
    deadline.  Already-running pairs finish at their next pair boundary; replay
    remains responsible for finer-grained attempt cancellation/checkpointing.
    """

    bundle = load_measurement_schedule_bundle(
        store,
        run_id=run_id,
        measurement_plan_fingerprint=measurement_plan_fingerprint,
    )
    verified_decision = bundle.isolation_decision
    if bundle.plan.isolation_decision_fingerprint != verified_decision.fingerprint:
        raise ValueError("scheduler isolation decision differs from frozen plan")
    if bundle.plan.evidence_policy_fingerprint != bundle.evidence_policy_profile.fingerprint:
        raise ValueError("scheduler evidence policy differs from frozen plan")
    if (
        isinstance(configured_lane_limit, bool)
        or not isinstance(configured_lane_limit, int)
        or configured_lane_limit <= 0
    ):
        raise ValueError("configured_lane_limit must be positive")
    if checkpoint_quantum_seconds is not None and (
        isinstance(checkpoint_quantum_seconds, bool)
        or not isinstance(checkpoint_quantum_seconds, (int, float))
        or not math.isfinite(float(checkpoint_quantum_seconds))
        or checkpoint_quantum_seconds <= 0
    ):
        raise ValueError("checkpoint_quantum_seconds must be positive")
    if campaign_deadline_monotonic is not None and (
        isinstance(campaign_deadline_monotonic, bool)
        or not isinstance(campaign_deadline_monotonic, (int, float))
        or not math.isfinite(float(campaign_deadline_monotonic))
    ):
        raise ValueError("campaign_deadline_monotonic must be finite")
    if materialization_timeout_seconds is not None and (
        isinstance(materialization_timeout_seconds, bool)
        or not isinstance(materialization_timeout_seconds, (int, float))
        or not math.isfinite(float(materialization_timeout_seconds))
        or materialization_timeout_seconds <= 0
    ):
        raise ValueError("materialization_timeout_seconds must be positive")
    if not isinstance(lane_materializer, FrameworkFilesystemLaneMaterializer):
        raise TypeError(
            "authoritative scheduling requires FrameworkFilesystemLaneMaterializer"
        )

    frozen_items = bundle.work_items(
        admitted_stage_ids=admitted_stage_ids,
        admitted_case_ids=admitted_case_ids,
    )
    coordinates = tuple(item.coordinate for item in frozen_items)
    if len(coordinates) != len(set(coordinates)):
        raise ValueError("pair work coordinates must be unique")
    unit_ids = tuple(
        unit_id
        for item in frozen_items
        for unit_id in (
            item.control_work_unit_id,
            item.treatment_work_unit_id,
        )
    )
    if len(unit_ids) != len(set(unit_ids)):
        raise ValueError("pair work-unit ids must be unique")
    expected_reused_control_ids = {
        item.control_work_unit_id for item in frozen_items if item.control_reused
    }
    if expected_reused_control_ids and resolve_reused_control is None:
        raise ValueError(
            "store-verified reused controls require a trusted observation resolver"
        )

    lane_count = min(
        2,
        configured_lane_limit,
        verified_decision.safe_lane_count,
    )
    def read_clock() -> float:
        value = clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ValueError("scheduler clock must return a finite number")
        return float(value)

    started_at = read_clock()
    ordered = tuple(
        sorted(
            frozen_items,
            key=lambda item: (-item.priority, item.stage_id, item.case_id, item.repetition_id),
        )
    )
    pending_queue = deque(ordered)

    completed: list[PairLaneResult[ControlT, TreatmentT]] = []
    completed_coordinates: set[tuple[str, int]] = set()
    resumable_incomplete_coordinates: set[tuple[str, int]] = set()
    stop_kind: PairLaneStopKind | None = None
    stop_reason: str | None = None

    async def emit(event: str, **fields: object) -> None:
        if observer is None:
            return
        result = observer(event, fields)
        if inspect.isawaitable(result):
            await result

    def scheduling_boundary() -> tuple[PairLaneStopKind, str] | None:
        now = read_clock()
        if campaign_deadline_monotonic is not None and now >= campaign_deadline_monotonic:
            return (
                PairLaneStopKind.CAMPAIGN_DEADLINE,
                "campaign wall deadline reached at a resumable pair boundary",
            )
        if (
            checkpoint_quantum_seconds is not None
            and now - started_at >= checkpoint_quantum_seconds
        ):
            return (
                PairLaneStopKind.CHECKPOINT_QUANTUM,
                "checkpoint quantum reached at a resumable pair boundary",
            )
        return None

    async def record_stop(kind: PairLaneStopKind, reason: str) -> None:
        nonlocal stop_kind, stop_reason
        if stop_kind is None:
            stop_kind = kind
            stop_reason = reason
            await emit("schedule_stop", kind=kind.value, reason=reason)

    async def execute_pair(
        item: PairLaneWorkItem,
        lane_id: int,
    ) -> tuple[
        PairLaneResult[ControlT, TreatmentT],
        tuple[PairLaneStopKind, str] | None,
    ]:
        context = lane_contexts[lane_id]
        await emit(
            "pair_started",
            lane_id=lane_id,
            case_id=item.case_id,
            repetition_id=item.repetition_id,
        )
        if item.control_reused:
            assert resolve_reused_control is not None
            observation = bundle.observation_by_work_unit[
                item.control_work_unit_id
            ]
            resolved = await resolve_reused_control(observation, context)
            if not isinstance(resolved, ResolvedControl):
                raise TypeError("reused control resolver must return ResolvedControl")
            if resolved.result_fingerprint != observation.result_fingerprint:
                raise ValueError(
                    "reused control payload does not match immutable observation"
                )
            control = resolved.value
            await emit(
                "control_reused",
                lane_id=lane_id,
                case_id=item.case_id,
                repetition_id=item.repetition_id,
                work_unit_id=item.control_work_unit_id,
            )
        else:
            control = await run_control(item, context)
        treatment: TreatmentT | None = None
        admitted = (
            True
            if control_allows_treatment is None
            else control_allows_treatment(item, control)
        )
        boundary = scheduling_boundary()
        if boundary is not None:
            admitted = False
            resumable_incomplete_coordinates.add(item.coordinate)
        if admitted:
            treatment = await run_treatment(item, context, control)
        result = PairLaneResult(
            item=item,
            context=context,
            control=control,
            treatment=treatment,
            treatment_admitted=admitted,
        )
        await emit(
            "pair_completed",
            lane_id=lane_id,
            case_id=item.case_id,
            repetition_id=item.repetition_id,
            treatment_admitted=admitted,
        )
        return result, boundary

    active: dict[
        asyncio.Task[
            tuple[
                PairLaneResult[ControlT, TreatmentT],
                tuple[PairLaneStopKind, str] | None,
            ]
        ],
        int,
    ] = {}
    available_lanes = set(range(1, lane_count + 1))
    canonical_grants = verified_decision.grant_set.grants
    planned_lane_contexts = {
        lane_id: LaneExecutionContext(
            lane_id=lane_id,
            measurement_plan_fingerprint=bundle.plan.measurement_plan_fingerprint,
            isolation_decision_fingerprint=verified_decision.fingerprint,
            evidence_policy_fingerprint=bundle.evidence_policy_profile.fingerprint,
            grant=(
                canonical_grants[lane_id - 1]
                if lane_id <= len(canonical_grants)
                else None
            ),
        )
        for lane_id in range(1, lane_count + 1)
    }
    materialization_limit = float(
        materialization_timeout_seconds
        if materialization_timeout_seconds is not None
        else bundle.plan.deadlines.attempt_timeout_seconds
    )
    boundary_limits: list[tuple[PairLaneStopKind, float]] = []
    now = read_clock()
    if campaign_deadline_monotonic is not None:
        boundary_limits.append(
            (
                PairLaneStopKind.CAMPAIGN_DEADLINE,
                max(0.0, campaign_deadline_monotonic - now),
            )
        )
    if checkpoint_quantum_seconds is not None:
        boundary_limits.append(
            (
                PairLaneStopKind.CHECKPOINT_QUANTUM,
                max(0.0, checkpoint_quantum_seconds - (now - started_at)),
            )
        )
    limiting_boundary = min(boundary_limits, key=lambda item: item[1]) if boundary_limits else None
    effective_materialization_limit = min(
        materialization_limit,
        limiting_boundary[1] if limiting_boundary is not None else materialization_limit,
    )
    materialization_tasks = tuple(
        asyncio.create_task(lane_materializer.materialize(context))
        for context in planned_lane_contexts.values()
    )
    try:
        materialized_lanes = await asyncio.wait_for(
            asyncio.gather(*materialization_tasks),
            timeout=effective_materialization_limit,
        )
    except asyncio.TimeoutError:
        for task in materialization_tasks:
            task.cancel()
        await asyncio.gather(*materialization_tasks, return_exceptions=True)
        if (
            limiting_boundary is not None
            and limiting_boundary[1] <= materialization_limit
        ):
            kind = limiting_boundary[0]
            return PairLaneScheduleResult(
                stop_kind=kind,
                stop_reason=(
                    "lane materialization reached a resumable scheduling boundary"
                ),
                safe_lane_count=lane_count,
                isolation_decision_fingerprint=verified_decision.fingerprint,
                completed=(),
                pending=ordered,
                elapsed_seconds=max(0.0, read_clock() - started_at),
            )
        raise TimeoutError("lane materialization hard deadline exceeded") from None

    lane_contexts: dict[int, LaneExecutionContext] = {}
    for context, materialized in zip(
        planned_lane_contexts.values(), materialized_lanes, strict=True
    ):
        writer = issue_framework_evidence_writer_attestation_v2(
            bundle.evidence_policy_profile,
            writer_identity=f"measurement-writer-lane-{context.lane_id}",
            isolation_identity=(
                context.grant.fingerprint
                if context.grant is not None
                else verified_decision.fingerprint
            ),
            resource_identity=stable_control_fingerprint(
                {
                    "topology": materialized.topology.to_dict(),
                    "bindings": list(materialized.binding_fingerprints),
                }
            ),
        )
        actual_grant = IsolationGrant.create(
            topology=materialized.topology,
            binding_fingerprints=materialized.binding_fingerprints,
        )
        if context.grant is not None and actual_grant != context.grant:
            raise ValueError(
                "materialized topology does not match planned isolation grant"
            )
        attestation = store._issue_lane_materialization_attestation(
            run_id,
            bundle.plan.measurement_plan_fingerprint,
            lane_id=context.lane_id,
            isolation_grant_fingerprint=(
                context.grant.fingerprint if context.grant is not None else None
            ),
            topology=materialized.topology,
            binding_fingerprints=materialized.binding_fingerprints,
            writer_attestation=writer,
            claims=materialized.claims,
            recorded_at=datetime.now(timezone.utc).isoformat(),
        )
        lane_contexts[context.lane_id] = LaneExecutionContext(
            lane_id=context.lane_id,
            measurement_plan_fingerprint=context.measurement_plan_fingerprint,
            isolation_decision_fingerprint=context.isolation_decision_fingerprint,
            evidence_policy_fingerprint=context.evidence_policy_fingerprint,
            grant=context.grant,
            lane_materialization_fingerprint=attestation.attestation_fingerprint,
            evidence_writer_attestation_fingerprint=writer.fingerprint,
            lane_attestation=attestation,
        )

    async def admit_available_pairs() -> None:
        while stop_kind is None and pending_queue and available_lanes:
            boundary = scheduling_boundary()
            if boundary is not None:
                await record_stop(*boundary)
                return
            lane_id = min(available_lanes)
            available_lanes.remove(lane_id)
            item = pending_queue.popleft()
            task = asyncio.create_task(
                execute_pair(item, lane_id),
                name=f"measurement-pair-lane-{lane_id}",
            )
            active[task] = lane_id

    await emit(
        "schedule_started",
        safe_lane_count=lane_count,
        planned_pair_count=len(ordered),
        isolation_decision_fingerprint=verified_decision.fingerprint,
    )
    await admit_available_pairs()
    try:
        while active:
            finished, _pending = await asyncio.wait(
                tuple(active), return_when=asyncio.FIRST_COMPLETED
            )
            for task in sorted(finished, key=lambda item: active[item]):
                lane_id = active.pop(task)
                available_lanes.add(lane_id)
                pair_result, boundary = task.result()
                completed.append(pair_result)
                completed_coordinates.add(pair_result.item.coordinate)
                if boundary is not None:
                    await record_stop(*boundary)
                if stop_kind is None and should_stop is not None:
                    completed_snapshot = tuple(
                        sorted(completed, key=lambda result: ordered.index(result.item))
                    )
                    decision = should_stop(completed_snapshot)
                    if inspect.isawaitable(decision):
                        decision = await decision
                    if decision:
                        await record_stop(PairLaneStopKind.DECISIVE_STOP, decision)
            await admit_available_pairs()
    except BaseException:
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        raise

    if stop_kind is None:
        stop_kind = PairLaneStopKind.COMPLETED
        stop_reason = "all admitted pair work reached a terminal boundary"
    pending = tuple(
        item
        for item in ordered
        if item.coordinate not in completed_coordinates
        or item.coordinate in resumable_incomplete_coordinates
    )
    await emit(
        "schedule_completed",
        stop_kind=stop_kind.value,
        completed_pair_count=len(completed),
        pending_pair_count=len(pending),
    )
    return PairLaneScheduleResult(
        stop_kind=stop_kind,
        stop_reason=stop_reason or stop_kind.value,
        safe_lane_count=lane_count,
        isolation_decision_fingerprint=verified_decision.fingerprint,
        completed=tuple(sorted(completed, key=lambda result: ordered.index(result.item))),
        pending=pending,
        elapsed_seconds=max(0.0, read_clock() - started_at),
    )
