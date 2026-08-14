"""Durable work-unit execution transitions for Measurement Control Plane v2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aworld.self_evolve.measurement_control import (
    LaneMaterializationAttestationV1,
    MeasurementControlEventKind,
    MeasurementControlObservationRecord,
    MeasurementPlanV2,
    MeasurementWorkUnitIndexEntry,
    MeasurementWorkUnitState,
    WorkUnitJournalEvent,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore


@dataclass(frozen=True)
class MeasurementAttemptHandle:
    work_unit_id: str
    attempt_id: str
    started_at: str
    lease_expires_at: str


class MeasurementExecutionJournal:
    """Exactly-once transition facade used by replay scheduler callbacks."""

    def __init__(
        self,
        *,
        store: FilesystemSelfEvolveStore,
        run_id: str,
        plan: MeasurementPlanV2,
    ) -> None:
        self._store = store
        self._run_id = run_id
        stored = store.read_measurement_control_plan(
            run_id, plan.measurement_plan_fingerprint
        )
        if stored != plan:
            raise ValueError("execution journal plan differs from persisted plan")
        self._plan = stored

    @property
    def plan(self) -> MeasurementPlanV2:
        return self._plan

    def recover_expired(self, *, now: str) -> tuple[str, ...]:
        return self._store.recover_expired_measurement_control_leases(
            self._run_id,
            self._plan.measurement_plan_fingerprint,
            now=now,
        )

    def index_entries(self) -> tuple[MeasurementWorkUnitIndexEntry, ...]:
        return self._store.read_measurement_control_index(
            self._run_id, self._plan.measurement_plan_fingerprint
        ).work_units

    def terminal_observations(
        self,
    ) -> dict[str, MeasurementControlObservationRecord]:
        observations: dict[str, MeasurementControlObservationRecord] = {}
        for entry in self.index_entries():
            if not entry.state.terminal:
                continue
            if entry.observation_fingerprint is None:
                raise ValueError("terminal work unit has no immutable observation")
            observations[entry.work_unit_id] = (
                self._store.read_measurement_control_observation(
                    self._run_id,
                    self._plan.measurement_plan_fingerprint,
                    entry.observation_fingerprint,
                )
            )
        return observations

    def begin(
        self,
        *,
        work_unit_id: str,
        attempt_id: str,
        now: str,
    ) -> MeasurementAttemptHandle:
        entry = self._entry(work_unit_id)
        if entry.state not in {
            MeasurementWorkUnitState.PENDING,
            MeasurementWorkUnitState.CHECKPOINTED,
        }:
            raise ValueError("work unit is not admissible for a new attempt")
        started = _utc(now)
        lease_expiry = started + timedelta(
            seconds=self._plan.deadlines.member_hard_deadline_seconds
        )
        lease_expires_at = _format_utc(lease_expiry)
        lease = WorkUnitJournalEvent.create(
            measurement_plan_fingerprint=self._plan.measurement_plan_fingerprint,
            work_unit_id=work_unit_id,
            kind=MeasurementControlEventKind.LEASE_ACQUIRED,
            previous_state=entry.state,
            new_state=MeasurementWorkUnitState.LEASED,
            occurred_at=now,
            attempt_id=attempt_id,
            lease_expires_at=lease_expires_at,
        )
        self._append(lease)
        execution_started = WorkUnitJournalEvent.create(
            measurement_plan_fingerprint=self._plan.measurement_plan_fingerprint,
            work_unit_id=work_unit_id,
            kind=MeasurementControlEventKind.EXECUTION_STARTED,
            previous_state=MeasurementWorkUnitState.LEASED,
            new_state=MeasurementWorkUnitState.RUNNING,
            occurred_at=now,
            attempt_id=attempt_id,
            lease_expires_at=lease_expires_at,
        )
        self._append(execution_started)
        return MeasurementAttemptHandle(
            work_unit_id=work_unit_id,
            attempt_id=attempt_id,
            started_at=now,
            lease_expires_at=lease_expires_at,
        )

    def checkpoint(
        self,
        handle: MeasurementAttemptHandle,
        *,
        now: str,
        attempt_cost_seconds: float,
        reason_code: str,
    ) -> None:
        self._require_active_handle(handle)
        event = WorkUnitJournalEvent.create(
            measurement_plan_fingerprint=self._plan.measurement_plan_fingerprint,
            work_unit_id=handle.work_unit_id,
            kind=MeasurementControlEventKind.CHECKPOINT_RECORDED,
            previous_state=MeasurementWorkUnitState.RUNNING,
            new_state=MeasurementWorkUnitState.CHECKPOINTED,
            occurred_at=now,
            attempt_id=handle.attempt_id,
            attempt_cost_seconds=attempt_cost_seconds,
            reason_code=reason_code,
        )
        self._append(event)

    def terminal(
        self,
        handle: MeasurementAttemptHandle,
        *,
        terminal_state: MeasurementWorkUnitState,
        result_fingerprint: str,
        lane_attestation: LaneMaterializationAttestationV1,
        now: str,
        attempt_cost_seconds: float,
        reason_code: str | None = None,
    ) -> MeasurementControlObservationRecord:
        self._require_active_handle(handle)
        if not terminal_state.terminal:
            raise ValueError("terminal transition requires a terminal state")
        if not isinstance(lane_attestation, LaneMaterializationAttestationV1):
            raise TypeError("terminal transition requires a typed lane attestation")
        if (
            lane_attestation.measurement_plan_fingerprint
            != self._plan.measurement_plan_fingerprint
        ):
            raise ValueError("lane attestation belongs to a different measurement plan")
        self._store.write_lane_materialization_attestation(
            self._run_id,
            self._plan.measurement_plan_fingerprint,
            lane_attestation,
        )
        observation = MeasurementControlObservationRecord.create(
            plan=self._plan,
            work_unit_id=handle.work_unit_id,
            terminal_state=terminal_state,
            result_fingerprint=result_fingerprint,
            isolation_grant_fingerprint=(
                lane_attestation.isolation_grant_fingerprint
            ),
            lane_materialization_fingerprint=(
                lane_attestation.attestation_fingerprint
            ),
            recorded_at=now,
        )
        self._store.write_measurement_control_observation(
            self._run_id,
            self._plan.measurement_plan_fingerprint,
            observation,
        )
        event = WorkUnitJournalEvent.create(
            measurement_plan_fingerprint=self._plan.measurement_plan_fingerprint,
            work_unit_id=handle.work_unit_id,
            kind=MeasurementControlEventKind.TERMINAL_RECORDED,
            previous_state=MeasurementWorkUnitState.RUNNING,
            new_state=terminal_state,
            occurred_at=now,
            attempt_id=handle.attempt_id,
            observation_fingerprint=observation.observation_fingerprint,
            attempt_cost_seconds=attempt_cost_seconds,
            reason_code=reason_code,
        )
        self._append(event)
        return observation

    def _entry(self, work_unit_id: str) -> MeasurementWorkUnitIndexEntry:
        entry = next(
            (item for item in self.index_entries() if item.work_unit_id == work_unit_id),
            None,
        )
        if entry is None:
            raise ValueError("work unit is outside the persisted measurement plan")
        return entry

    def _require_active_handle(self, handle: MeasurementAttemptHandle) -> None:
        entry = self._entry(handle.work_unit_id)
        if (
            entry.state is not MeasurementWorkUnitState.RUNNING
            or entry.active_attempt_id != handle.attempt_id
        ):
            raise ValueError("attempt handle does not own the active work unit")

    def _append(self, event: WorkUnitJournalEvent) -> None:
        self._store.append_measurement_control_event(
            self._run_id,
            self._plan.measurement_plan_fingerprint,
            event,
        )


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
