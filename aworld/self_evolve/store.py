from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - required mode fails closed below.
    fcntl = None  # type: ignore[assignment]

from aworld.self_evolve.atomic_fs import atomic_exchange_paths
from aworld.self_evolve.ingestion.types import (
    FrozenIngestionSnapshot,
    fingerprint_json,
)
from aworld.self_evolve.ingestion.semantic_snapshot import (
    FROZEN_SEMANTIC_INGESTION_SNAPSHOT_SCHEMA_VERSION,
    FrozenSemanticIngestionSnapshotV2,
)
from aworld.self_evolve.ingestion.verifier import (
    validate_frozen_snapshot_quality,
)
from aworld.self_evolve.evaluation_plan import (
    HumanEvidenceApprovalV1,
    ManifestOrigin,
)
from aworld.self_evolve.budget import (
    CandidateAttemptEvent,
    CandidateAttemptKey,
    validate_candidate_attempt_lifecycle,
)
from aworld.self_evolve.provenance import TargetProvenance
from aworld.self_evolve.candidate_package import (
    candidate_package_fingerprint,
    validate_candidate_files,
)
from aworld.self_evolve.replay_adaptation import ReplayPreflightReport
from aworld.self_evolve.replay_adaptation import IsolationDecision, ReplayIsolationTopology
from aworld.core.tool.replay_policy import EvidencePolicyProfileV2
from aworld.core.tool.replay_policy import FrameworkEvidenceWriterAttestationV2
from aworld.core.tool.replay_policy import (
    issue_framework_evidence_writer_attestation_v2,
)
from aworld.self_evolve.regression import RegressionEvidence, RegressionSuiteSpec
from aworld.self_evolve.challenger import ChallengeReport
from aworld.self_evolve.judge import JudgeRecord
from aworld.self_evolve.measurement import (
    AttributionReport,
    ControlledExperimentSpec,
    MeasurementObservation,
)
from aworld.self_evolve.measurement_control import (
    LaneMaterializationAttestationV1,
    LaneMaterializationClaim,
    MeasurementControlObservationRecord,
    LegacyMeasurementControlDescription,
    MeasurementControlCorruptionError,
    MeasurementControlEventKind,
    MeasurementControlIndex,
    MeasurementControlSnapshot,
    MeasurementPlanV2,
    MeasurementWorkUnitState,
    WorkUnitJournalEvent,
    advance_measurement_control_index,
    classify_work_unit_reuse,
    describe_legacy_measurement_control,
    extend_measurement_control_journal_fingerprint,
    initial_measurement_control_index,
    measurement_control_bytes_fingerprint,
    rebuild_measurement_control_index,
    stable_control_fingerprint,
)
from aworld.self_evolve.sanitization import public_diagnostic_projection
from aworld.self_evolve.credit_assignment import TargetSelectionReport
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    OptimizerLineage,
    SelfEvolveRun,
    SelfEvolveRunStatus,
    to_json_dict,
)
from aworld.skills.release import mark_skill_content_candidate


_MEASUREMENT_JOURNAL_MAX_EVENTS = 4096
_MEASUREMENT_JOURNAL_MAX_BYTES = 16 * 1024 * 1024
_MEASUREMENT_JOURNAL_MAX_EVENT_BYTES = 65_536
_MEASUREMENT_COMPACTION_INTENT_SCHEMA = (
    "aworld.self_evolve.measurement_compaction_intent.v1"
)
_MEASUREMENT_COMPACTION_METADATA_MAX_BYTES = 16 * 1024 * 1024


def _ingestion_semantic_payload(
    snapshot: FrozenIngestionSnapshot | FrozenSemanticIngestionSnapshotV2,
) -> dict[str, Any]:
    payload = snapshot.to_dict(public=False)
    if isinstance(snapshot, FrozenSemanticIngestionSnapshotV2):
        return payload
    payload.pop("mapping_candidates", None)
    payload.pop("mapping_failures", None)
    payload.pop("ingestion_model_call_count", None)
    quality = payload.get("quality_report")
    if isinstance(quality, dict):
        quality.pop("mapping_candidate_count", None)
        quality.pop("valid_mapping_candidate_count", None)
    return payload


def _semantic_evidence_approval_template(
    snapshot: FrozenSemanticIngestionSnapshotV2,
) -> dict[str, Any] | None:
    if (
        snapshot.manifest_fingerprint is None
        or snapshot.manifest_origin.value != "operator_explicit"
        or snapshot.resolution_evidence.extraction_origin.value
        == "deterministic_canonical"
    ):
        return None
    approval = HumanEvidenceApprovalV1(
        evidence_graph_logical_fingerprint=(
            snapshot.evidence_graph.logical_fingerprint
        ),
        evidence_graph_provenance_fingerprint=(
            snapshot.evidence_graph.provenance_fingerprint
        ),
        source_bundle_fingerprint=snapshot.source_bundle.fingerprint,
        constitution_fingerprint=snapshot.constitution.fingerprint,
        semantic_profile_fingerprint=(
            snapshot.semantic_profile.fingerprint
        ),
        manifest_fingerprint=snapshot.manifest_fingerprint,
        approval_origin=ManifestOrigin.OPERATOR_EXPLICIT,
        approved_claim_scope=("whole_graph",),
    )
    return {
        **approval.to_dict(),
        "approval_fingerprint": approval.fingerprint,
    }


class FilesystemSelfEvolveStore:
    """Filesystem artifact store under `.aworld/self_evolve/<run_id>/`."""

    def __init__(self, workspace_root: str | Path, artifact_root: str | Path | None = None) -> None:
        self.workspace_root = Path(workspace_root)
        self.artifact_root = (
            Path(artifact_root)
            if artifact_root is not None
            else self.workspace_root / ".aworld" / "self_evolve"
        )
        self._measurement_authority_private_keys: dict[
            Path, Ed25519PrivateKey
        ] = {}

    def run_path(self, run_id: str) -> Path:
        self._validate_id(run_id, "run_id")
        return self.artifact_root / run_id

    def campaign_path(self, campaign_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,159}", campaign_id):
            raise ValueError(f"invalid campaign_id: {campaign_id!r}")
        return self.artifact_root / "campaigns" / campaign_id

    def measurement_experiment_path(
        self,
        run_id: str,
        experiment_id: str,
    ) -> Path:
        """Return the bounded artifact root for one controlled experiment."""

        self._validate_id(run_id, "run_id")
        if not re.fullmatch(r"experiment-[0-9a-f]{32}", experiment_id):
            raise ValueError(f"invalid experiment_id: {experiment_id!r}")
        return self.run_path(run_id) / "experiments" / experiment_id

    def measurement_attribution_ref(
        self,
        run_id: str,
        experiment_id: str,
    ) -> str:
        self.measurement_experiment_path(run_id, experiment_id)
        return (
            Path("experiments")
            / experiment_id
            / "attribution_report.json"
        ).as_posix()

    def measurement_control_plan_path(
        self,
        run_id: str,
        measurement_plan_fingerprint: str,
    ) -> Path:
        """Return a path addressed only by a validated canonical plan digest."""

        self._validate_id(run_id, "run_id")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", measurement_plan_fingerprint):
            raise ValueError("invalid measurement plan fingerprint")
        digest = measurement_plan_fingerprint.removeprefix("sha256:")
        return self.run_path(run_id) / "measurement_control" / f"plan-{digest}"

    def write_measurement_control_plan(
        self,
        run_id: str,
        plan: MeasurementPlanV2,
        *,
        isolation_decision: IsolationDecision,
        evidence_policy_profile: EvidencePolicyProfileV2,
    ) -> Path:
        """Persist an immutable plan before any work-unit transition."""

        if not isinstance(plan, MeasurementPlanV2):
            raise TypeError("measurement control plan must be typed")
        try:
            validated_plan = MeasurementPlanV2.from_dict(
                plan.to_dict(),
                isolation_decision=isolation_decision,
                evidence_policy_profile=evidence_policy_profile,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("measurement control plan failed canonical validation") from exc
        if validated_plan != plan:
            raise ValueError("measurement control plan failed canonical validation")
        experiment = self.read_measurement_experiment(run_id, plan.experiment_id)
        if experiment.experiment_id != plan.experiment_id:
            raise ValueError("measurement plan does not belong to the experiment")
        root = self.measurement_control_plan_path(
            run_id, plan.measurement_plan_fingerprint
        )
        self._reject_symlink_components(root.parent)
        if root.is_symlink() or (root.exists() and not root.is_dir()):
            raise ValueError("measurement control plan destination is unsafe")
        plan_path = root / "plan.json"
        isolation_path = root / "isolation_decision.json"
        evidence_path = root / "evidence_policy_profile.json"
        if plan_path.exists():
            if plan_path.is_symlink() or not plan_path.is_file():
                raise ValueError("immutable measurement plan destination is unsafe")
            try:
                existing = self.read_measurement_control_plan(
                    run_id, plan.measurement_plan_fingerprint
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(
                    "immutable measurement plan destination is invalid"
                ) from exc
            if existing.to_dict() != plan.to_dict():
                raise ValueError(
                    "immutable measurement plan already exists with different content"
                )
            stored_decision, stored_profile = self.read_measurement_control_contracts(
                run_id, plan.measurement_plan_fingerprint
            )
            if stored_decision != isolation_decision:
                raise ValueError("immutable isolation decision already differs")
            if stored_profile != evidence_policy_profile:
                raise ValueError("immutable evidence policy profile already differs")
        else:
            if root.exists():
                raise ValueError("immutable measurement plan destination is incomplete")
            root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            staging = root.parent / f".{root.name}.tmp-{uuid.uuid4().hex}"
            staging.mkdir(mode=0o700)
            try:
                self._write_json_atomic(
                    staging / "isolation_decision.json",
                    isolation_decision.to_dict(),
                )
                self._write_json_atomic(
                    staging / "evidence_policy_profile.json",
                    evidence_policy_profile.to_dict(),
                )
                self._write_json_atomic(staging / "plan.json", plan.to_dict())
                authority_private_key = self._create_measurement_authority_key(
                    staging / "authority.pub"
                )
                self._create_empty_private_file(staging / "journal.jsonl")
                self._write_json_atomic(
                    staging / "index.json",
                    initial_measurement_control_index(plan).to_dict(),
                )
                os.replace(staging, root)
                self._measurement_authority_private_keys[
                    root.resolve()
                ] = authority_private_key
                self._fsync_directory(root.parent)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise

        journal_path = root / "journal.jsonl"
        self._read_measurement_authority_public_key(root)
        if not journal_path.exists():
            self._create_empty_private_file(journal_path)
        elif journal_path.is_symlink() or not journal_path.is_file():
            raise ValueError("measurement control journal destination is unsafe")

        index_path = root / "index.json"
        if not index_path.exists():
            if journal_path.stat().st_size != 0:
                raise MeasurementControlCorruptionError(
                    "measurement_index_missing_with_journal",
                    "measurement journal exists without its canonical index",
                )
            self._write_json_atomic(
                index_path, initial_measurement_control_index(plan).to_dict()
            )
        elif index_path.is_symlink() or not index_path.is_file():
            raise ValueError("measurement control index destination is unsafe")

        reloaded = self.read_measurement_control_plan(
            run_id, plan.measurement_plan_fingerprint
        )
        if reloaded != plan:
            raise ValueError("persisted measurement control plan did not round trip")
        self.read_measurement_control_index(
            run_id, plan.measurement_plan_fingerprint
        )
        return root

    def read_measurement_control_contracts(
        self,
        run_id: str,
        measurement_plan_fingerprint: str,
    ) -> tuple[IsolationDecision, EvidencePolicyProfileV2]:
        """Load and revalidate the authority-bearing plan artifacts."""

        root = self.measurement_control_plan_path(
            run_id, measurement_plan_fingerprint
        )
        self._reject_symlink_components(root)
        isolation_path = root / "isolation_decision.json"
        evidence_path = root / "evidence_policy_profile.json"
        for path in (isolation_path, evidence_path):
            if path.is_symlink() or not path.is_file():
                raise FileNotFoundError("measurement control contract artifact not found")
        isolation_decision = IsolationDecision.from_dict(
            self._read_json(isolation_path)
        )
        evidence_policy_profile = EvidencePolicyProfileV2.from_dict(
            self._read_json(evidence_path)
        )
        return isolation_decision, evidence_policy_profile

    def read_measurement_control_plan(
        self,
        run_id: str,
        measurement_plan_fingerprint: str,
    ) -> MeasurementPlanV2:
        path = self.measurement_control_plan_path(
            run_id, measurement_plan_fingerprint
        ) / "plan.json"
        self._reject_symlink_components(path.parent)
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("measurement control plan not found")
        isolation_decision, evidence_policy_profile = (
            self.read_measurement_control_contracts(
                run_id, measurement_plan_fingerprint
            )
        )
        plan = MeasurementPlanV2.from_dict(
            self._read_json(path),
            isolation_decision=isolation_decision,
            evidence_policy_profile=evidence_policy_profile,
        )
        if plan.measurement_plan_fingerprint != measurement_plan_fingerprint:
            raise ValueError("measurement plan identity does not match its path")
        self.read_measurement_experiment(run_id, plan.experiment_id)
        return plan

    def read_measurement_control_journal(
        self,
        run_id: str,
        measurement_plan_fingerprint: str,
    ) -> tuple[WorkUnitJournalEvent, ...]:
        root = self.measurement_control_plan_path(
            run_id, measurement_plan_fingerprint
        )
        with self._measurement_control_append_lock(root):
            plan = self.read_measurement_control_plan(
                run_id, measurement_plan_fingerprint
            )
            index = self._read_measurement_control_index_unlocked(
                run_id, measurement_plan_fingerprint, verify_prefix=True
            )
            journal_bytes = self._read_measurement_control_journal_bytes(
                self._measurement_control_journal_path(root, index)
            )
            return self._decode_measurement_control_journal(plan, journal_bytes)

    def read_measurement_control_index(
        self,
        run_id: str,
        measurement_plan_fingerprint: str,
    ) -> MeasurementControlIndex:
        """Read, validate, and recover a journal tail left before index replace."""

        root = self.measurement_control_plan_path(
            run_id, measurement_plan_fingerprint
        )
        with self._measurement_control_append_lock(root):
            return self._read_measurement_control_index_unlocked(
                run_id, measurement_plan_fingerprint, verify_prefix=True
            )

    def _read_measurement_control_index_unlocked(
        self,
        run_id: str,
        measurement_plan_fingerprint: str,
        *,
        verify_prefix: bool,
    ) -> MeasurementControlIndex:
        plan = self.read_measurement_control_plan(
            run_id, measurement_plan_fingerprint
        )
        root = self.measurement_control_plan_path(run_id, measurement_plan_fingerprint)
        index_path = root / "index.json"
        self._recover_measurement_compaction_unlocked(root, plan)
        if index_path.is_symlink() or not index_path.is_file():
            raise MeasurementControlCorruptionError(
                "measurement_index_missing",
                "canonical measurement index is missing or unsafe",
            )
        try:
            stored = MeasurementControlIndex.from_dict(self._read_json(index_path))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MeasurementControlCorruptionError(
                "measurement_index_invalid",
                "canonical measurement index is invalid",
            ) from exc
        if stored.measurement_plan_fingerprint != measurement_plan_fingerprint:
            raise MeasurementControlCorruptionError(
                "measurement_index_plan_identity_mismatch",
                "canonical index references a different measurement plan",
            )
        journal_path = self._measurement_control_journal_path(root, stored)
        snapshot: MeasurementControlSnapshot | None = None
        if stored.snapshot_fingerprint is not None:
            digest = stored.snapshot_fingerprint.removeprefix("sha256:")
            snapshot_path = root / "snapshots" / f"snapshot-{digest}.json"
            try:
                snapshot = MeasurementControlSnapshot.from_dict(
                    self._read_json(snapshot_path)
                )
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise MeasurementControlCorruptionError(
                    "measurement_snapshot_invalid",
                    "canonical compacted index has no verified snapshot",
                ) from exc
            if (
                snapshot.snapshot_fingerprint != stored.snapshot_fingerprint
                or snapshot.measurement_plan_fingerprint
                != measurement_plan_fingerprint
                or snapshot.compacted_event_count != stored.compacted_event_count
            ):
                raise MeasurementControlCorruptionError(
                    "measurement_snapshot_index_mismatch",
                    "snapshot does not match canonical compacted index",
                )
        journal_size = self._measurement_control_journal_size(journal_path)
        if journal_size > _MEASUREMENT_JOURNAL_MAX_BYTES:
            self._record_oversized_measurement_journal(
                root,
                journal_file=stored.journal_file,
                observed_bytes=journal_size,
                confirmed_bytes=stored.journal_byte_count,
            )
            raise MeasurementControlCorruptionError(
                "measurement_journal_oversized",
                "measurement journal exceeds its hard read bound",
            )
        if journal_size < stored.journal_byte_count:
            raise MeasurementControlCorruptionError(
                "measurement_journal_prefix_corrupt",
                "journal bytes confirmed by the canonical index were modified",
            )
        if verify_prefix:
            with journal_path.open("rb") as handle:
                prefix = handle.read(stored.journal_byte_count)
            if measurement_control_bytes_fingerprint(prefix) != stored.journal_sha256:
                raise MeasurementControlCorruptionError(
                    "measurement_journal_prefix_corrupt",
                    "journal bytes confirmed by the canonical index were modified",
                )
            if snapshot is None:
                base_index = initial_measurement_control_index(plan)
            else:
                base_index = MeasurementControlIndex(
                    measurement_plan_fingerprint=plan.measurement_plan_fingerprint,
                    work_units=snapshot.work_units,
                    event_count=snapshot.compacted_event_count,
                    observation_count=snapshot.observation_count,
                    actual_attempt_cost_seconds=snapshot.actual_attempt_cost_seconds,
                    journal_byte_count=0,
                    journal_sha256=measurement_control_bytes_fingerprint(b""),
                    journal_file=stored.journal_file,
                    compacted_event_count=snapshot.compacted_event_count,
                    snapshot_fingerprint=snapshot.snapshot_fingerprint,
                )
            rebuilt = rebuild_measurement_control_index(
                plan,
                self._decode_measurement_control_journal(plan, prefix),
                journal_bytes=prefix,
                base_index=base_index,
            )
            if rebuilt != stored:
                raise MeasurementControlCorruptionError(
                    "measurement_index_journal_mismatch",
                    "canonical index is not derived from its snapshot and journal",
                )
        if journal_size == stored.journal_byte_count:
            return stored
        with journal_path.open("rb") as handle:
            handle.seek(stored.journal_byte_count)
            tail = handle.read(
                _MEASUREMENT_JOURNAL_MAX_BYTES - stored.journal_byte_count + 1
            )
        if len(tail) > _MEASUREMENT_JOURNAL_MAX_BYTES - stored.journal_byte_count:
            raise MeasurementControlCorruptionError(
                "measurement_journal_oversized",
                "measurement journal tail exceeds its hard read bound",
            )
        last_newline = tail.rfind(b"\n")
        complete_tail = tail[: last_newline + 1] if last_newline >= 0 else b""
        torn_tail = tail[last_newline + 1 :] if last_newline >= 0 else tail
        if torn_tail:
            torn_digest = hashlib.sha256(torn_tail).hexdigest()
            if len(torn_tail) <= _MEASUREMENT_JOURNAL_MAX_EVENT_BYTES:
                quarantine = root / "quarantine" / f"torn-tail-{torn_digest}.bin"
                self._write_bytes_atomic(quarantine, torn_tail)
            else:
                self._write_json_atomic(
                    root / "quarantine" / f"torn-tail-{torn_digest}.json",
                    {
                        "schema_version": "aworld.measurement_journal_quarantine.v1",
                        "journal_file": stored.journal_file,
                        "torn_tail_bytes": len(torn_tail),
                        "torn_tail_sha256": f"sha256:{torn_digest}",
                        "content_copied": False,
                        "reason_code": "measurement_journal_torn_record",
                    },
                )
            descriptor = os.open(
                journal_path,
                os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                os.ftruncate(descriptor, stored.journal_byte_count + len(complete_tail))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        current = stored
        for event, encoded in self._decode_measurement_control_journal_records(
            plan, complete_tail
        ):
            next_count = current.journal_byte_count + len(encoded)
            next_hash = extend_measurement_control_journal_fingerprint(
                current.journal_sha256, encoded
            )
            current = advance_measurement_control_index(
                plan,
                current,
                event,
                journal_byte_count=next_count,
                journal_sha256=next_hash,
            )
        if complete_tail:
            self._write_json_atomic(index_path, current.to_dict())
        return current

    def append_measurement_control_event(
        self,
        run_id: str,
        measurement_plan_fingerprint: str,
        event: WorkUnitJournalEvent,
    ) -> Path:
        """Append one bounded transition and atomically advance its index."""

        root = self.measurement_control_plan_path(
            run_id, measurement_plan_fingerprint
        )
        with self._measurement_control_append_lock(root):
            return self._append_measurement_control_event_unlocked(
                run_id, measurement_plan_fingerprint, event
            )

    def _append_measurement_control_event_unlocked(
        self,
        run_id: str,
        measurement_plan_fingerprint: str,
        event: WorkUnitJournalEvent,
    ) -> Path:

        if not isinstance(event, WorkUnitJournalEvent):
            raise TypeError("measurement control event must be typed")
        if event.measurement_plan_fingerprint != measurement_plan_fingerprint:
            raise ValueError("event references a different measurement plan")
        plan = self.read_measurement_control_plan(
            run_id, measurement_plan_fingerprint
        )
        current_index = self._read_measurement_control_index_unlocked(
            run_id, measurement_plan_fingerprint, verify_prefix=False
        )
        root = self.measurement_control_plan_path(run_id, measurement_plan_fingerprint)
        journal_path = self._measurement_control_journal_path(root, current_index)
        known_event_ids = {
            entry.last_event_id for entry in current_index.work_units
            if entry.last_event_id is not None
        } | {
            attempt.finalized_event_id
            for entry in current_index.work_units
            for attempt in entry.finalized_attempts
        }
        if event.event_id in known_event_ids:
            return journal_path
        if event.kind is MeasurementControlEventKind.TERMINAL_RECORDED:
            self._validate_terminal_observation(
                run_id, plan, event
            )
        # Ensures transition, attempt, and terminal invariants before mutation.
        encoded = (
            json.dumps(
                event.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > _MEASUREMENT_JOURNAL_MAX_EVENT_BYTES:
            raise ValueError("measurement control journal event exceeds 64 KiB")
        if (
            current_index.event_count - current_index.compacted_event_count
            >= _MEASUREMENT_JOURNAL_MAX_EVENTS
            or current_index.journal_byte_count + len(encoded)
            > _MEASUREMENT_JOURNAL_MAX_BYTES
        ):
            raise MeasurementControlCorruptionError(
                "measurement_journal_compaction_required",
                "bounded journal requires a verified snapshot before append",
            )
        next_index = advance_measurement_control_index(
            plan,
            current_index,
            event,
            journal_byte_count=current_index.journal_byte_count + len(encoded),
            journal_sha256=extend_measurement_control_journal_fingerprint(
                current_index.journal_sha256, encoded
            ),
        )
        self._reject_symlink_components(journal_path.parent)
        if journal_path.is_symlink() or not journal_path.is_file():
            raise ValueError("measurement control journal destination is unsafe")
        descriptor = os.open(
            journal_path,
            os.O_APPEND | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            self._write_all(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        journal_path.chmod(0o600)
        self._write_json_atomic(root / "index.json", next_index.to_dict())
        return journal_path

    def recover_expired_measurement_control_leases(
        self,
        run_id: str,
        measurement_plan_fingerprint: str,
        *,
        now: str,
    ) -> tuple[str, ...]:
        """Checkpoint expired active units without turning expiry into failure."""

        current_time = self._parse_utc_datetime(now, "now")
        plan = self.read_measurement_control_plan(
            run_id, measurement_plan_fingerprint
        )
        index = self.read_measurement_control_index(
            run_id, measurement_plan_fingerprint
        )
        recovered: list[str] = []
        for entry in index.work_units:
            if entry.state not in {
                MeasurementWorkUnitState.LEASED,
                MeasurementWorkUnitState.RUNNING,
            } or entry.lease_expires_at is None:
                continue
            if self._parse_utc_datetime(
                entry.lease_expires_at, "lease_expires_at"
            ) > current_time:
                continue
            event = WorkUnitJournalEvent.create(
                measurement_plan_fingerprint=plan.measurement_plan_fingerprint,
                work_unit_id=entry.work_unit_id,
                kind=MeasurementControlEventKind.LEASE_RECOVERED,
                previous_state=entry.state,
                new_state=MeasurementWorkUnitState.CHECKPOINTED,
                occurred_at=now,
                attempt_id=entry.active_attempt_id or "missing-attempt",
                attempt_cost_seconds=min(
                    plan.deadlines.member_hard_deadline_seconds,
                    max(
                        0.0,
                        (
                            current_time
                            - self._parse_utc_datetime(
                                entry.active_attempt_started_at
                                or entry.last_occurred_at
                                or now,
                                "active_attempt_started_at",
                            )
                        ).total_seconds(),
                    ),
                ),
                reason_code="lease_expired",
            )
            self.append_measurement_control_event(
                run_id, measurement_plan_fingerprint, event
            )
            recovered.append(entry.work_unit_id)
        return tuple(recovered)

    def write_measurement_control_observation(
        self,
        run_id: str,
        measurement_plan_fingerprint: str,
        observation: MeasurementControlObservationRecord,
    ) -> Path:
        """Persist one immutable terminal observation before journal completion."""

        if not isinstance(observation, MeasurementControlObservationRecord):
            raise TypeError("measurement control observation must be typed")
        plan = self.read_measurement_control_plan(run_id, measurement_plan_fingerprint)
        self._validate_observation_coordinates(run_id, plan, observation)
        digest = observation.observation_fingerprint.removeprefix("sha256:")
        path = self.measurement_control_plan_path(
            run_id, measurement_plan_fingerprint
        ) / "observations" / f"observation-{digest}.json"
        if path.exists():
            existing = MeasurementControlObservationRecord.from_dict(
                self._read_json(path)
            )
            if existing != observation:
                raise ValueError("immutable observation digest has conflicting content")
            return path
        self._write_json_atomic(path, observation.to_dict())
        reloaded = self.read_measurement_control_observation(
            run_id, measurement_plan_fingerprint, observation.observation_fingerprint
        )
        if reloaded != observation:
            raise ValueError("persisted measurement observation did not round trip")
        return path

    def write_lane_materialization_attestation(
        self,
        run_id: str,
        measurement_plan_fingerprint: str,
        attestation: LaneMaterializationAttestationV1,
    ) -> Path:
        """Persist framework materialization proof before any lane executes."""

        if not isinstance(attestation, LaneMaterializationAttestationV1):
            raise TypeError("lane materialization attestation must be typed")
        plan = self.read_measurement_control_plan(run_id, measurement_plan_fingerprint)
        decision, profile = self.read_measurement_control_contracts(
            run_id, measurement_plan_fingerprint
        )
        self._validate_lane_materialization_attestation(
            run_id, plan, decision, profile, attestation
        )
        digest = attestation.attestation_fingerprint.removeprefix("sha256:")
        path = self.measurement_control_plan_path(
            run_id, measurement_plan_fingerprint
        ) / "lane-attestations" / f"attestation-{digest}.json"
        if path.exists():
            existing = LaneMaterializationAttestationV1.from_dict(
                self._read_json(path)
            )
            if existing != attestation:
                raise ValueError(
                    "immutable lane attestation digest has conflicting content"
                )
            return path
        self._write_json_atomic(path, attestation.to_dict())
        reloaded = self.read_lane_materialization_attestation(
            run_id,
            measurement_plan_fingerprint,
            attestation.attestation_fingerprint,
        )
        if reloaded != attestation:
            raise ValueError("persisted lane attestation did not round trip")
        return path

    def _issue_lane_materialization_attestation(
        self,
        run_id: str,
        measurement_plan_fingerprint: str,
        *,
        lane_id: int,
        isolation_grant_fingerprint: str | None,
        topology: ReplayIsolationTopology,
        binding_fingerprints: tuple[str, ...],
        writer_attestation: FrameworkEvidenceWriterAttestationV2,
        claims: tuple[LaneMaterializationClaim, ...],
        recorded_at: str,
    ) -> LaneMaterializationAttestationV1:
        """Sign a lane proof with authority never exposed to replay children."""

        plan = self.read_measurement_control_plan(run_id, measurement_plan_fingerprint)
        decision, profile = self.read_measurement_control_contracts(
            run_id, measurement_plan_fingerprint
        )
        if not isinstance(topology, ReplayIsolationTopology) or not isinstance(
            writer_attestation, FrameworkEvidenceWriterAttestationV2
        ):
            raise TypeError("lane proof requires typed materializer provenance")
        expected_isolation_identity = (
            isolation_grant_fingerprint or decision.fingerprint
        )
        expected_resource_identity = stable_control_fingerprint(
            {
                "topology": topology.to_dict(),
                "bindings": list(sorted(binding_fingerprints)),
            }
        )
        if (
            writer_attestation.evidence_policy_fingerprint != profile.fingerprint
            or writer_attestation.writer_identity
            != f"measurement-writer-lane-{lane_id}"
            or writer_attestation.isolation_identity != expected_isolation_identity
            or writer_attestation.resource_identity != expected_resource_identity
        ):
            raise ValueError("framework writer attestation does not match the lane")
        root = self.measurement_control_plan_path(
            run_id, measurement_plan_fingerprint
        )
        private_key = self._measurement_authority_private_key(root)
        authority_public_key_fingerprint = (
            self._measurement_authority_public_key_fingerprint(root)
        )
        unsigned = LaneMaterializationAttestationV1.create(
            measurement_plan_fingerprint=plan.measurement_plan_fingerprint,
            isolation_decision_fingerprint=decision.fingerprint,
            evidence_policy_fingerprint=profile.fingerprint,
            lane_id=lane_id,
            isolation_grant_fingerprint=isolation_grant_fingerprint,
            topology_fingerprint=topology.topology_fingerprint,
            topology=topology.to_dict(),
            writer_attestation_fingerprint=writer_attestation.fingerprint,
            writer_attestation=writer_attestation.to_dict(),
            claims=claims,
            recorded_at=recorded_at,
            authority_public_key_fingerprint=authority_public_key_fingerprint,
            authority_signature="0" * 128,
        )
        signature = private_key.sign(
            json.dumps(
                unsigned.authority_payload(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hex()
        attestation = LaneMaterializationAttestationV1.create(
            measurement_plan_fingerprint=plan.measurement_plan_fingerprint,
            isolation_decision_fingerprint=decision.fingerprint,
            evidence_policy_fingerprint=profile.fingerprint,
            lane_id=lane_id,
            isolation_grant_fingerprint=isolation_grant_fingerprint,
            topology_fingerprint=topology.topology_fingerprint,
            topology=topology.to_dict(),
            writer_attestation_fingerprint=writer_attestation.fingerprint,
            writer_attestation=writer_attestation.to_dict(),
            claims=claims,
            recorded_at=recorded_at,
            authority_public_key_fingerprint=authority_public_key_fingerprint,
            authority_signature=signature,
        )
        self.write_lane_materialization_attestation(
            run_id, measurement_plan_fingerprint, attestation
        )
        return attestation

    def read_lane_materialization_attestation(
        self,
        run_id: str,
        measurement_plan_fingerprint: str,
        attestation_fingerprint: str,
    ) -> LaneMaterializationAttestationV1:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", attestation_fingerprint):
            raise ValueError("invalid lane attestation fingerprint")
        plan = self.read_measurement_control_plan(run_id, measurement_plan_fingerprint)
        decision, profile = self.read_measurement_control_contracts(
            run_id, measurement_plan_fingerprint
        )
        digest = attestation_fingerprint.removeprefix("sha256:")
        path = self.measurement_control_plan_path(
            run_id, measurement_plan_fingerprint
        ) / "lane-attestations" / f"attestation-{digest}.json"
        self._reject_symlink_components(path.parent)
        if path.is_symlink() or not path.is_file():
            raise MeasurementControlCorruptionError(
                "lane_materialization_attestation_missing",
                "lane materialization attestation is missing or unsafe",
            )
        try:
            attestation = LaneMaterializationAttestationV1.from_dict(
                self._read_json(path)
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MeasurementControlCorruptionError(
                "lane_materialization_attestation_invalid",
                "lane materialization attestation failed content verification",
            ) from exc
        if attestation.attestation_fingerprint != attestation_fingerprint:
            raise MeasurementControlCorruptionError(
                "lane_materialization_attestation_path_mismatch",
                "lane attestation identity does not match its immutable path",
            )
        self._validate_lane_materialization_attestation(
            run_id, plan, decision, profile, attestation
        )
        return attestation

    def read_measurement_control_observation(
        self,
        run_id: str,
        measurement_plan_fingerprint: str,
        observation_fingerprint: str,
    ) -> MeasurementControlObservationRecord:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", observation_fingerprint):
            raise ValueError("invalid observation fingerprint")
        plan = self.read_measurement_control_plan(run_id, measurement_plan_fingerprint)
        digest = observation_fingerprint.removeprefix("sha256:")
        path = self.measurement_control_plan_path(
            run_id, measurement_plan_fingerprint
        ) / "observations" / f"observation-{digest}.json"
        self._reject_symlink_components(path.parent)
        if path.is_symlink() or not path.is_file():
            raise MeasurementControlCorruptionError(
                "measurement_observation_missing",
                "terminal observation record is missing or unsafe",
            )
        try:
            observation = MeasurementControlObservationRecord.from_dict(
                self._read_json(path)
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MeasurementControlCorruptionError(
                "measurement_observation_invalid",
                "terminal observation record failed content-address verification",
            ) from exc
        if observation.observation_fingerprint != observation_fingerprint:
            raise MeasurementControlCorruptionError(
                "measurement_observation_path_mismatch",
                "observation identity does not match its immutable path",
            )
        self._validate_observation_coordinates(run_id, plan, observation)
        return observation

    def resolve_compatible_measurement_control_observation(
        self,
        run_id: str,
        *,
        expected_plan: MeasurementPlanV2,
        stored_plan_fingerprint: str,
        stored_work_unit_id: str,
    ) -> MeasurementControlObservationRecord:
        """Resolve reuse only through typed plan compatibility and a real record."""

        stored_plan = self.read_measurement_control_plan(
            run_id, stored_plan_fingerprint
        )
        index = self.read_measurement_control_index(run_id, stored_plan_fingerprint)
        entry = index.entry(stored_work_unit_id)
        decision = classify_work_unit_reuse(
            expected_plan=expected_plan,
            stored_plan=stored_plan,
            stored_entry=entry,
        )
        if not decision.compatible or decision.observation_fingerprint is None:
            raise MeasurementControlCorruptionError(
                "measurement_observation_reuse_incompatible",
                f"stored observation is not reusable: {decision.reason_code}",
            )
        return self.read_measurement_control_observation(
            run_id, stored_plan_fingerprint, decision.observation_fingerprint
        )

    def compact_measurement_control_journal(
        self, run_id: str, measurement_plan_fingerprint: str
    ) -> Path:
        """Publish a verified snapshot and a new journal generation atomically."""

        root = self.measurement_control_plan_path(run_id, measurement_plan_fingerprint)
        with self._measurement_control_append_lock(root):
            index = self._read_measurement_control_index_unlocked(
                run_id, measurement_plan_fingerprint, verify_prefix=True
            )
            if index.event_count == index.compacted_event_count:
                raise ValueError("measurement journal has no uncompacted events")
            snapshot = MeasurementControlSnapshot.create(index)
            digest = snapshot.snapshot_fingerprint.removeprefix("sha256:")
            snapshot_path = root / "snapshots" / f"snapshot-{digest}.json"
            self._write_json_atomic(snapshot_path, snapshot.to_dict())
            self._fsync_directory(snapshot_path.parent)
            verified = MeasurementControlSnapshot.from_dict(self._read_json(snapshot_path))
            if verified != snapshot:
                raise MeasurementControlCorruptionError(
                    "measurement_snapshot_verification_failed",
                    "measurement snapshot did not round trip",
                )
            target_journal_file = f"journal-{digest}.jsonl"
            target_journal_path = root / target_journal_file
            if not target_journal_path.exists():
                self._write_bytes_atomic(target_journal_path, b"")
            elif (
                target_journal_path.is_symlink()
                or not target_journal_path.is_file()
                or target_journal_path.stat().st_size != 0
            ):
                raise MeasurementControlCorruptionError(
                    "measurement_compaction_target_unsafe",
                    "new journal generation is not an empty regular file",
                )
            self._fsync_directory(root)
            compacted = MeasurementControlIndex(
                measurement_plan_fingerprint=index.measurement_plan_fingerprint,
                work_units=index.work_units,
                event_count=index.event_count,
                observation_count=index.observation_count,
                actual_attempt_cost_seconds=index.actual_attempt_cost_seconds,
                journal_byte_count=0,
                journal_sha256=measurement_control_bytes_fingerprint(b""),
                journal_file=target_journal_file,
                compacted_event_count=index.event_count,
                snapshot_fingerprint=snapshot.snapshot_fingerprint,
            )
            intent = {
                "schema_version": _MEASUREMENT_COMPACTION_INTENT_SCHEMA,
                "measurement_plan_fingerprint": measurement_plan_fingerprint,
                "source_index_fingerprint": index.index_fingerprint,
                "source_journal_file": index.journal_file,
                "snapshot_fingerprint": snapshot.snapshot_fingerprint,
                "target_index": compacted.to_dict(),
            }
            self._write_json_atomic(root / "compaction.json", intent)
            self._fsync_directory(root)
            self._commit_measurement_compaction_unlocked(root, plan=None)
            return snapshot_path

    def _recover_measurement_compaction_unlocked(
        self, root: Path, plan: MeasurementPlanV2
    ) -> None:
        intent_path = root / "compaction.json"
        if not intent_path.exists():
            return
        self._commit_measurement_compaction_unlocked(root, plan=plan)

    def _commit_measurement_compaction_unlocked(
        self, root: Path, *, plan: MeasurementPlanV2 | None
    ) -> None:
        """Recover or commit one prepared generation switch under the store lock."""

        intent_path = root / "compaction.json"
        try:
            intent = self._read_bounded_json(
                intent_path, _MEASUREMENT_COMPACTION_METADATA_MAX_BYTES
            )
            if intent.get("schema_version") != _MEASUREMENT_COMPACTION_INTENT_SCHEMA:
                raise ValueError("unsupported compaction intent schema")
            plan_fingerprint = str(intent.get("measurement_plan_fingerprint") or "")
            if plan is not None and plan.measurement_plan_fingerprint != plan_fingerprint:
                raise ValueError("compaction intent references a different plan")
            source_fingerprint = str(intent.get("source_index_fingerprint") or "")
            source_journal_file = str(intent.get("source_journal_file") or "")
            if not re.fullmatch(
                r"journal(?:-[0-9a-f]{64})?\.jsonl", source_journal_file
            ):
                raise ValueError("compaction source journal identity is invalid")
            snapshot_fingerprint = str(intent.get("snapshot_fingerprint") or "")
            target_index = MeasurementControlIndex.from_dict(
                self._require_mapping(intent.get("target_index"), "target_index")
            )
            if (
                target_index.measurement_plan_fingerprint != plan_fingerprint
                or target_index.snapshot_fingerprint != snapshot_fingerprint
                or source_journal_file == target_index.journal_file
            ):
                raise ValueError("compaction intent identities are inconsistent")
            current_index = MeasurementControlIndex.from_dict(
                self._read_bounded_json(
                    root / "index.json", _MEASUREMENT_COMPACTION_METADATA_MAX_BYTES
                )
            )
            snapshot_digest = snapshot_fingerprint.removeprefix("sha256:")
            snapshot = MeasurementControlSnapshot.from_dict(
                self._read_bounded_json(
                    root / "snapshots" / f"snapshot-{snapshot_digest}.json",
                    _MEASUREMENT_COMPACTION_METADATA_MAX_BYTES,
                )
            )
            if (
                snapshot.snapshot_fingerprint != snapshot_fingerprint
                or snapshot.measurement_plan_fingerprint != plan_fingerprint
                or snapshot.compacted_event_count != target_index.compacted_event_count
                or target_index.event_count != target_index.compacted_event_count
                or snapshot.work_units != target_index.work_units
                or snapshot.observation_count != target_index.observation_count
                or snapshot.actual_attempt_cost_seconds
                != target_index.actual_attempt_cost_seconds
                or target_index.journal_byte_count != 0
                or target_index.journal_sha256
                != measurement_control_bytes_fingerprint(b"")
            ):
                raise ValueError("compaction snapshot does not match target index")
            self._validate_measurement_journal_generation(root, target_index)
            if current_index.index_fingerprint == source_fingerprint:
                if current_index.journal_file != source_journal_file:
                    raise ValueError("compaction source journal identity drifted")
                self._validate_measurement_journal_generation(root, current_index)
                self._write_json_atomic(root / "index.json", target_index.to_dict())
                self._fsync_directory(root)
                current_index = target_index
            elif current_index.index_fingerprint != target_index.index_fingerprint:
                raise ValueError("compaction authority is neither source nor target")
            self._validate_measurement_journal_generation(root, current_index)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MeasurementControlCorruptionError(
                "measurement_compaction_recovery_failed",
                "prepared measurement compaction could not be verified",
            ) from exc
        source_path = root / source_journal_file
        if source_path.parent != root:
            raise MeasurementControlCorruptionError(
                "measurement_compaction_source_unsafe",
                "obsolete journal generation escaped its plan root",
            )
        if source_path.name != target_index.journal_file and source_path.exists():
            if source_path.is_symlink() or not source_path.is_file():
                raise MeasurementControlCorruptionError(
                    "measurement_compaction_source_unsafe",
                    "obsolete journal generation is unsafe",
                )
            source_path.unlink()
            self._fsync_directory(root)
        intent_path.unlink(missing_ok=True)
        self._fsync_directory(root)

    def _validate_measurement_journal_generation(
        self, root: Path, index: MeasurementControlIndex
    ) -> None:
        journal_path = self._measurement_control_journal_path(root, index)
        size = self._measurement_control_journal_size(journal_path)
        if size != index.journal_byte_count or size > _MEASUREMENT_JOURNAL_MAX_BYTES:
            raise ValueError("journal generation size does not match its index")
        payload = self._read_measurement_control_journal_bytes(journal_path)
        if measurement_control_bytes_fingerprint(payload) != index.journal_sha256:
            raise ValueError("journal generation fingerprint does not match its index")

    @staticmethod
    def _require_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{field_name} must be an object")
        return value

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _create_measurement_authority_key(path: Path) -> Ed25519PrivateKey:
        private_key = Ed25519PrivateKey.generate()
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            written = os.write(descriptor, public_bytes)
            if written != len(public_bytes):
                raise OSError("short measurement authority public-key write")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return private_key

    def _read_measurement_authority_public_key(
        self,
        root: Path,
        authority_fingerprint: str | None = None,
    ) -> Ed25519PublicKey:
        current_path = root / "authority.pub"
        path = current_path
        if authority_fingerprint is not None:
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", authority_fingerprint):
                raise MeasurementControlCorruptionError(
                    "measurement_authority_key_invalid",
                    "measurement authority fingerprint is invalid",
                )
            current_bytes = self._read_measurement_authority_public_bytes(current_path)
            current_fingerprint = "sha256:" + hashlib.sha256(current_bytes).hexdigest()
            if current_fingerprint != authority_fingerprint:
                path = (
                    root
                    / "authority-history"
                    / f"authority-{authority_fingerprint.removeprefix('sha256:')}.pub"
                )
        key = self._read_measurement_authority_public_bytes(path)
        observed_fingerprint = "sha256:" + hashlib.sha256(key).hexdigest()
        if (
            authority_fingerprint is not None
            and observed_fingerprint != authority_fingerprint
        ):
            raise MeasurementControlCorruptionError(
                "measurement_authority_key_invalid",
                "measurement authority key does not match its content address",
            )
        return Ed25519PublicKey.from_public_bytes(key)

    def _read_measurement_authority_public_bytes(self, path: Path) -> bytes:
        root = path.parent if path.name == "authority.pub" else path.parent.parent
        self._reject_symlink_components(root)
        if path.is_symlink() or not path.is_file():
            raise MeasurementControlCorruptionError(
                "measurement_authority_key_missing",
                "measurement authority key is missing or unsafe",
            )
        mode = path.stat(follow_symlinks=False).st_mode & 0o777
        if mode & 0o077:
            raise MeasurementControlCorruptionError(
                "measurement_authority_key_permissions",
                "measurement authority key permissions are too broad",
            )
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            key = os.read(descriptor, 33)
        finally:
            os.close(descriptor)
        if len(key) != 32:
            raise MeasurementControlCorruptionError(
                "measurement_authority_key_invalid",
                "measurement authority key length is invalid",
            )
        return key

    def _measurement_authority_public_key_fingerprint(self, root: Path) -> str:
        key = self._read_measurement_authority_public_bytes(root / "authority.pub")
        return "sha256:" + hashlib.sha256(key).hexdigest()

    def _measurement_authority_private_key(
        self, root: Path
    ) -> Ed25519PrivateKey:
        private_key = self._measurement_authority_private_keys.get(root.resolve())
        if private_key is None:
            private_key = self._rotate_measurement_authority(root)
            self._measurement_authority_private_keys[root.resolve()] = private_key
        return private_key

    def _rotate_measurement_authority(self, root: Path) -> Ed25519PrivateKey:
        """Create a new process authority while retaining old verification keys.

        Rotation is a framework-owned resume operation. Existing observations
        retain the content address of their original public key; new pending
        work is signed by the new process-local key.
        """

        current_path = root / "authority.pub"
        current = self._read_measurement_authority_public_bytes(current_path)
        current_fingerprint = "sha256:" + hashlib.sha256(current).hexdigest()
        history = root / "authority-history"
        history.mkdir(mode=0o700, exist_ok=True)
        history_path = history / (
            f"authority-{current_fingerprint.removeprefix('sha256:')}.pub"
        )
        if not history_path.exists():
            self._write_bytes_atomic(history_path, current)
        elif self._read_measurement_authority_public_bytes(history_path) != current:
            raise MeasurementControlCorruptionError(
                "measurement_authority_history_conflict",
                "measurement authority history has conflicting content",
            )
        next_path = root / f".authority.next-{uuid.uuid4().hex}"
        private_key = self._create_measurement_authority_key(next_path)
        os.replace(next_path, current_path)
        self._fsync_directory(root)
        return private_key

    def read_legacy_measurement_control_description(
        self, run_id: str, relative_ref: str
    ) -> LegacyMeasurementControlDescription:
        """Read a bounded diagnostic projection; never return reusable state."""

        self._validate_id(run_id, "run_id")
        relative = Path(relative_ref)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError("legacy measurement reference must be a safe relative path")
        root = self.run_path(run_id)
        path = root / relative
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("legacy measurement reference escapes its run directory")
        self._reject_symlink_components(path.parent)
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("legacy measurement checkpoint not found")
        if path.stat().st_size > 65_536:
            raise ValueError("legacy measurement checkpoint exceeds 64 KiB")
        payload = self._read_json(path)
        if not isinstance(payload, Mapping):
            raise ValueError("legacy measurement checkpoint must be an object")
        return describe_legacy_measurement_control(payload)

    def _validate_terminal_observation(
        self,
        run_id: str,
        plan: MeasurementPlanV2,
        event: WorkUnitJournalEvent,
    ) -> None:
        observation = self.read_measurement_control_observation(
            run_id,
            plan.measurement_plan_fingerprint,
            event.observation_fingerprint or "",
        )
        if (
            observation.work_unit_id != event.work_unit_id
            or observation.terminal_state is not event.new_state
        ):
            raise MeasurementControlCorruptionError(
                "measurement_observation_event_mismatch",
                "terminal event does not match immutable observation coordinates",
            )

    def _validate_observation_coordinates(
        self,
        run_id: str,
        plan: MeasurementPlanV2,
        observation: MeasurementControlObservationRecord,
    ) -> None:
        unit = next(
            (item for item in plan.work_units if item.work_unit_id == observation.work_unit_id),
            None,
        )
        if unit is None or (
            observation.measurement_plan_fingerprint
            != plan.measurement_plan_fingerprint
            or observation.experiment_id != unit.experiment_id
            or observation.case_id != unit.case_id
            or observation.arm is not unit.arm
            or observation.repetition_id != unit.repetition_id
        ):
            raise MeasurementControlCorruptionError(
                "measurement_observation_coordinate_mismatch",
                "observation coordinates do not match the frozen work unit",
            )
        isolation_decision, profile = self.read_measurement_control_contracts(
            run_id, plan.measurement_plan_fingerprint
        )
        grant_fingerprints = {
            grant.fingerprint for grant in isolation_decision.grant_set.grants
        }
        observed_grant = observation.isolation_grant_fingerprint
        if grant_fingerprints:
            if observed_grant not in grant_fingerprints:
                raise MeasurementControlCorruptionError(
                    "measurement_observation_isolation_grant_mismatch",
                    "observation does not reference a grant from the frozen decision",
                )
        elif observed_grant is not None:
            raise MeasurementControlCorruptionError(
                "measurement_observation_unplanned_isolation_grant",
                "exclusive observation references an unplanned isolation grant",
            )
        attestation = self.read_lane_materialization_attestation(
            run_id,
            plan.measurement_plan_fingerprint,
            observation.lane_materialization_fingerprint,
        )
        self._validate_lane_materialization_attestation(
            run_id, plan, isolation_decision, profile, attestation
        )
        if attestation.isolation_grant_fingerprint != observed_grant:
            raise MeasurementControlCorruptionError(
                "measurement_observation_lane_attestation_mismatch",
                "observation grant differs from its lane materialization proof",
            )

    def _validate_lane_materialization_attestation(
        self,
        run_id: str,
        plan: MeasurementPlanV2,
        isolation_decision: IsolationDecision,
        profile: EvidencePolicyProfileV2,
        attestation: LaneMaterializationAttestationV1,
    ) -> None:
        if (
            attestation.measurement_plan_fingerprint
            != plan.measurement_plan_fingerprint
            or attestation.isolation_decision_fingerprint
            != isolation_decision.fingerprint
            or attestation.evidence_policy_fingerprint != profile.fingerprint
        ):
            raise MeasurementControlCorruptionError(
                "lane_materialization_attestation_contract_mismatch",
                "lane attestation differs from frozen plan contracts",
            )
        if attestation.lane_id > isolation_decision.safe_lane_count:
            raise MeasurementControlCorruptionError(
                "lane_materialization_attestation_lane_mismatch",
                "lane attestation exceeds the frozen safe lane count",
            )
        public_key = self._read_measurement_authority_public_key(
            self.measurement_control_plan_path(
                run_id, plan.measurement_plan_fingerprint
            ),
            attestation.authority_public_key_fingerprint,
        )
        try:
            public_key.verify(
                bytes.fromhex(attestation.authority_signature),
                json.dumps(
                    attestation.authority_payload(),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("utf-8"),
            )
        except (InvalidSignature, ValueError):
            raise MeasurementControlCorruptionError(
                "lane_materialization_attestation_authority_invalid",
                "lane attestation was not signed by the measurement store",
            ) from None
        try:
            topology_payload = json.loads(attestation.topology_json)
            writer_payload = json.loads(attestation.writer_attestation_json)
            topology = ReplayIsolationTopology.from_dict(
                self._require_mapping(topology_payload, "topology")
            )
            writer_mapping = self._require_mapping(
                writer_payload, "writer_attestation"
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MeasurementControlCorruptionError(
                "lane_materialization_attestation_provenance_invalid",
                "lane attestation provenance artifacts are invalid",
            ) from exc
        if set(writer_mapping) != {
            "evidence_policy_fingerprint",
            "writer_identity",
            "isolation_identity",
            "resource_identity",
        } or (
            topology.topology_fingerprint != attestation.topology_fingerprint
            or stable_control_fingerprint(writer_mapping)
            != attestation.writer_attestation_fingerprint
            or writer_mapping.get("evidence_policy_fingerprint")
            != profile.fingerprint
        ):
            raise MeasurementControlCorruptionError(
                "lane_materialization_attestation_provenance_mismatch",
                "lane attestation provenance differs from frozen contracts",
            )
        grants = {
            item.fingerprint: item for item in isolation_decision.grant_set.grants
        }
        grant_fingerprint = attestation.isolation_grant_fingerprint
        if grants:
            grant = grants.get(grant_fingerprint or "")
            if grant is None or grant.topology_fingerprint != attestation.topology_fingerprint:
                raise MeasurementControlCorruptionError(
                    "lane_materialization_attestation_grant_mismatch",
                    "lane attestation is not backed by a frozen isolation grant",
                )
            if topology.to_dict() != {
                "schema_version": topology.schema_version,
                "materializer_id": topology.materializer_id,
                "materializer_fingerprint": topology.materializer_fingerprint,
                "workspace_identity": topology.workspace_identity,
                "runtime_identity": topology.runtime_identity,
                "browser_profile_identity": topology.browser_profile_identity,
                "endpoint_namespace_identity": topology.endpoint_namespace_identity,
                "evidence_directory_identity": topology.evidence_directory_identity,
                "services": [item.to_dict() for item in topology.services],
                "resources": [item.to_dict() for item in topology.resources],
                "binding_coverage": [item.to_dict() for item in topology.binding_coverage],
                "cleanup_owner": topology.cleanup_owner,
                "topology_fingerprint": topology.topology_fingerprint,
            }:
                raise AssertionError("canonical topology serialization drifted")
        elif grant_fingerprint is not None:
            raise MeasurementControlCorruptionError(
                "lane_materialization_attestation_unplanned_grant",
                "exclusive lane attestation references an unplanned grant",
            )
        claim_paths = {
            item.dimension: item.declared_identity for item in attestation.claims
        }
        if grants:
            assert grant is not None
            expected_paths = {
                "workspace_root": grant.workspace_identity,
                "runtime_root": grant.runtime_identity,
                "browser_profile": grant.browser_profile_identity,
                "endpoint_namespace": grant.endpoint_namespace_identity,
                "evidence_directory": grant.evidence_directory_identity,
            }
            if claim_paths != expected_paths:
                raise MeasurementControlCorruptionError(
                    "lane_materialization_attestation_claim_mismatch",
                    "materialized resource claims differ from the frozen grant",
                )
            cleanup_owner = grant.cleanup_owner
            binding_fingerprints = grant.binding_fingerprints
            expected_isolation_identity = grant.fingerprint
        else:
            cleanup_owner = f"framework-lane-{attestation.lane_id}"
            binding_fingerprints = ()
            expected_isolation_identity = isolation_decision.fingerprint
            expected_topology = ReplayIsolationTopology.create(
                materializer_id="framework-filesystem-lane-materializer",
                materializer_fingerprint=stable_control_fingerprint(
                    {
                        "schema_version": "aworld.framework_lane_materializer.v1",
                        "kind": "filesystem",
                    }
                ),
                workspace_identity=claim_paths["workspace_root"],
                runtime_identity=claim_paths["runtime_root"],
                browser_profile_identity=claim_paths["browser_profile"],
                endpoint_namespace_identity=claim_paths["endpoint_namespace"],
                evidence_directory_identity=claim_paths["evidence_directory"],
                cleanup_owner=cleanup_owner,
            )
            if expected_topology.topology_fingerprint != attestation.topology_fingerprint:
                raise MeasurementControlCorruptionError(
                    "lane_materialization_attestation_topology_mismatch",
                    "exclusive lane topology is not the framework materializer topology",
                )
            if topology != expected_topology:
                raise MeasurementControlCorruptionError(
                    "lane_materialization_attestation_topology_payload_mismatch",
                    "exclusive lane topology payload is not canonical",
                )
        expected_writer = issue_framework_evidence_writer_attestation_v2(
            profile,
            writer_identity=f"measurement-writer-lane-{attestation.lane_id}",
            isolation_identity=expected_isolation_identity,
            resource_identity=stable_control_fingerprint(
                {
                    "topology": topology.to_dict(),
                    "bindings": list(sorted(binding_fingerprints)),
                }
            ),
        )
        if (
            writer_mapping != expected_writer.to_dict()
            or attestation.writer_attestation_fingerprint
            != expected_writer.fingerprint
        ):
            raise MeasurementControlCorruptionError(
                "lane_materialization_attestation_writer_mismatch",
                "lane writer provenance differs from the frozen lane contract",
            )
        marker_payload = stable_control_fingerprint(
            {
                "measurement_plan_fingerprint": plan.measurement_plan_fingerprint,
                "isolation_decision_fingerprint": isolation_decision.fingerprint,
                "lane_id": attestation.lane_id,
                "cleanup_owner": cleanup_owner,
            }
        )
        expected_marker_fingerprint = (
            "sha256:"
            + hashlib.sha256((marker_payload + "\n").encode("ascii")).hexdigest()
        )
        trusted_root = self.workspace_root.resolve()
        for claim in attestation.claims:
            path = Path(claim.declared_identity)
            marker = path / ".aworld-lane-owner"
            try:
                if (
                    not path.is_absolute()
                    or not path.resolve(strict=False).is_relative_to(trusted_root)
                    or path.is_symlink()
                    or not path.is_dir()
                    or marker.is_symlink()
                    or not marker.is_file()
                ):
                    raise ValueError("materialized claim path is missing or unsafe")
                stat = path.stat(follow_symlinks=False)
                marker_bytes = marker.read_bytes()
            except OSError as exc:
                raise MeasurementControlCorruptionError(
                    "lane_materialization_attestation_probe_failed",
                    "materialized resource could not be re-probed",
                ) from exc
            marker_fingerprint = "sha256:" + hashlib.sha256(marker_bytes).hexdigest()
            if (
                stat.st_dev != claim.observed_device
                or stat.st_ino != claim.observed_inode
                or marker_fingerprint != claim.ownership_marker_fingerprint
                or marker_fingerprint != expected_marker_fingerprint
            ):
                raise MeasurementControlCorruptionError(
                    "lane_materialization_attestation_probe_drift",
                    "materialized resource identity or ownership marker drifted",
                )

    def write_measurement_experiment(
        self,
        experiment: ControlledExperimentSpec,
    ) -> Path:
        if not isinstance(experiment, ControlledExperimentSpec):
            raise TypeError("measurement experiment must be typed")
        root = self.measurement_experiment_path(
            experiment.run_id,
            experiment.experiment_id,
        )
        path = root / "experiment.json"
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ValueError("measurement experiment destination is unsafe")
            existing = self.read_measurement_experiment(
                experiment.run_id,
                experiment.experiment_id,
            )
            if existing.to_dict() != experiment.to_dict():
                raise ValueError(
                    "immutable experiment id already exists with different content"
                )
            return path
        self._write_json_atomic(path, experiment.to_dict())
        reloaded = self.read_measurement_experiment(
            experiment.run_id,
            experiment.experiment_id,
        )
        if reloaded.to_dict() != experiment.to_dict():
            raise ValueError("persisted measurement experiment did not round trip")
        return path

    def read_measurement_experiment(
        self,
        run_id: str,
        experiment_id: str,
    ) -> ControlledExperimentSpec:
        path = (
            self.measurement_experiment_path(run_id, experiment_id)
            / "experiment.json"
        )
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(
                f"measurement experiment not found: {experiment_id}"
            )
        experiment = ControlledExperimentSpec.from_dict(self._read_json(path))
        if experiment.run_id != run_id or experiment.experiment_id != experiment_id:
            raise ValueError("measurement experiment identity does not match its path")
        return experiment

    def append_measurement_observations(
        self,
        run_id: str,
        experiment_id: str,
        observations: tuple[MeasurementObservation, ...],
    ) -> Path:
        """Idempotently append immutable, coordinate-addressed observations."""

        experiment = self.read_measurement_experiment(run_id, experiment_id)
        if any(not isinstance(item, MeasurementObservation) for item in observations):
            raise TypeError("measurement observations must be typed")
        for observation in observations:
            if (
                observation.run_id != run_id
                or observation.experiment_id != experiment_id
                or observation.swap_axis != experiment.swap_axis
            ):
                raise ValueError(
                    "measurement observation does not belong to the experiment"
                )
        path = self.measurement_experiment_path(run_id, experiment_id) / (
            "observations.jsonl"
        )
        self._reject_symlink_components(path.parent)
        if path.is_symlink():
            raise ValueError("measurement observation destination cannot be a symlink")
        existing = {
            item.observation_id: item
            for item in self.read_measurement_observations(
                run_id,
                experiment_id,
                missing_ok=True,
            )
        }
        additions: list[MeasurementObservation] = []
        pending: dict[str, MeasurementObservation] = {}
        for observation in observations:
            prior = existing.get(observation.observation_id) or pending.get(
                observation.observation_id
            )
            if prior is not None:
                if prior.to_dict() != observation.to_dict():
                    raise ValueError(
                        "immutable observation id already exists with different content"
                    )
                continue
            pending[observation.observation_id] = observation
            additions.append(observation)
        if not additions:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = "".join(
            json.dumps(
                item.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for item in additions
        ).encode("utf-8")
        descriptor = os.open(
            path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        path.chmod(0o600)
        return path

    def read_measurement_observations(
        self,
        run_id: str,
        experiment_id: str,
        *,
        missing_ok: bool = False,
    ) -> tuple[MeasurementObservation, ...]:
        self.read_measurement_experiment(run_id, experiment_id)
        path = self.measurement_experiment_path(run_id, experiment_id) / (
            "observations.jsonl"
        )
        if not path.exists():
            if missing_ok:
                return ()
            raise FileNotFoundError(
                f"measurement observations not found: {experiment_id}"
            )
        if path.is_symlink() or not path.is_file():
            raise ValueError("measurement observation artifact is unsafe")
        result: list[MeasurementObservation] = []
        identities: set[str] = set()
        for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not raw_line.strip():
                continue
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid measurement observation JSON at line {line_number}"
                ) from exc
            if not isinstance(payload, Mapping):
                raise ValueError(
                    f"measurement observation line {line_number} must be an object"
                )
            observation = MeasurementObservation.from_dict(payload)
            if observation.observation_id in identities:
                raise ValueError("measurement observation ids must be unique")
            if (
                observation.run_id != run_id
                or observation.experiment_id != experiment_id
            ):
                raise ValueError(
                    "measurement observation identity does not match its path"
                )
            identities.add(observation.observation_id)
            result.append(observation)
        return tuple(result)

    def write_measurement_attribution_report(
        self,
        report: AttributionReport,
    ) -> Path:
        if not isinstance(report, AttributionReport):
            raise TypeError("measurement attribution report must be typed")
        experiment = self.read_measurement_experiment(
            report.run_id,
            report.experiment_id,
        )
        if (
            report.mode != experiment.mode
            or report.swap_axis != experiment.swap_axis
        ):
            raise ValueError(
                "measurement attribution report does not match its experiment"
            )
        path = self.measurement_experiment_path(
            report.run_id,
            report.experiment_id,
        ) / "attribution_report.json"
        self._write_json_atomic(path, report.to_dict())
        reloaded = self.read_measurement_attribution_report(
            report.run_id,
            report.experiment_id,
        )
        if reloaded.to_dict() != report.to_dict():
            raise ValueError("persisted attribution report did not round trip")
        return path

    def read_measurement_attribution_report(
        self,
        run_id: str,
        experiment_id: str,
    ) -> AttributionReport:
        self.read_measurement_experiment(run_id, experiment_id)
        path = self.measurement_experiment_path(
            run_id,
            experiment_id,
        ) / "attribution_report.json"
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(
                f"measurement attribution report not found: {experiment_id}"
            )
        report = AttributionReport.from_dict(self._read_json(path))
        if report.run_id != run_id or report.experiment_id != experiment_id:
            raise ValueError("measurement attribution identity does not match its path")
        return report

    def ingestion_path(self, ingestion_id: str) -> Path:
        if not re.fullmatch(r"ingestion-[0-9a-f]{32}", ingestion_id):
            raise ValueError(f"invalid ingestion_id: {ingestion_id!r}")
        return self.artifact_root / "ingestions" / ingestion_id

    def write_ingestion(
        self,
        snapshot: FrozenIngestionSnapshot | FrozenSemanticIngestionSnapshotV2,
        *,
        dataset_recipe: DatasetRecipe | None = None,
    ) -> Path:
        if isinstance(snapshot, FrozenSemanticIngestionSnapshotV2):
            return self._write_semantic_ingestion(
                snapshot,
                dataset_recipe=dataset_recipe,
            )
        if not isinstance(snapshot, FrozenIngestionSnapshot):
            raise TypeError("ingestion snapshot must be typed")
        validate_frozen_snapshot_quality(snapshot)
        if any(
            case.source.ingestion_id != snapshot.ingestion_id
            for case in snapshot.normalized_cases
        ):
            raise ValueError(
                "normalized case provenance does not match ingestion identity"
            )
        destination = self.ingestion_path(snapshot.ingestion_id)
        expected = snapshot.to_dict(public=False)
        if destination.exists():
            if destination.is_symlink() or not destination.is_dir():
                raise ValueError("ingestion artifact destination is unsafe")
            existing = self.read_ingestion(snapshot.ingestion_id)
            if _ingestion_semantic_payload(
                existing
            ) != _ingestion_semantic_payload(snapshot):
                raise ValueError(
                    "immutable ingestion id already exists with different content"
                )
            return destination

        root = destination.parent
        self._reject_symlink_components(root)
        root.mkdir(parents=True, exist_ok=True)
        root.chmod(0o700)
        temporary = root / f".{snapshot.ingestion_id}.{uuid.uuid4().hex}.tmp"
        temporary.mkdir(mode=0o700)
        try:
            self._write_private_json(temporary / "ingestion.json", expected)
            self._write_private_json(
                temporary / "source_inventory.json",
                snapshot.inventory.to_dict(public=False),
            )
            self._write_private_json(
                temporary / "selected_mapping.json",
                snapshot.selected_mapping.to_dict(),
            )
            self._write_private_json(
                temporary / "structural_profile.json",
                {
                    asset.relative_path: dict(asset.structural_profile)
                    for asset in snapshot.inventory.assets
                },
            )
            mapping_candidates = (
                snapshot.mapping_candidates
                if snapshot.mapping_candidates
                else (snapshot.selected_mapping,)
            )
            for index, candidate in enumerate(mapping_candidates):
                self._write_private_json(
                    temporary
                    / "mapping_candidates"
                    / f"candidate-{index:03d}.json",
                    candidate.to_dict(),
                )
            if snapshot.mapping_failures:
                self._write_private_json(
                    temporary / "mapping_candidates" / "failures.json",
                    list(snapshot.mapping_failures),
                )
            if snapshot.source_manifest is not None:
                self._write_private_json(
                    temporary / "source_manifest.json",
                    snapshot.source_manifest,
                )
            self._write_private_json(
                temporary / "quality_report.json",
                snapshot.quality_report.to_dict(public=False),
            )
            self._write_private_jsonl(
                temporary / "rejected_records.jsonl",
                tuple(record.to_dict() for record in snapshot.rejected_records),
            )
            if dataset_recipe is None:
                self._write_private_jsonl(
                    temporary / "trainable_cases.jsonl",
                    tuple(case.to_dict() for case in snapshot.normalized_cases),
                )
                self._write_private_jsonl(
                    temporary / "held_out_cases.jsonl",
                    (),
                )
            else:
                trainable_ids = set(dataset_recipe.trainable_case_ids)
                held_out_ids = set(dataset_recipe.held_out_case_ids)
                self._write_private_jsonl(
                    temporary / "trainable_cases.jsonl",
                    tuple(
                        case.to_dict()
                        for case in snapshot.normalized_cases
                        if case.case_id in trainable_ids
                    ),
                )
                self._write_private_jsonl(
                    temporary / "held_out_cases.jsonl",
                    tuple(
                        case.to_dict()
                        for case in snapshot.normalized_cases
                        if case.case_id in held_out_ids
                    ),
                )
                self._write_private_json(
                    temporary / "dataset_recipe.json",
                    dataset_recipe,
                )
            os.replace(temporary, destination)
        except FileExistsError:
            existing = self.read_ingestion(snapshot.ingestion_id)
            if existing.to_dict(public=False) != expected:
                raise ValueError(
                    "immutable ingestion id already exists with different content"
                )
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        reloaded = self.read_ingestion(snapshot.ingestion_id)
        if reloaded.to_dict(public=False) != expected:
            raise ValueError("persisted ingestion snapshot did not round trip")
        return destination

    def read_ingestion(
        self,
        ingestion_id: str,
    ) -> FrozenIngestionSnapshot | FrozenSemanticIngestionSnapshotV2:
        root = self.ingestion_path(ingestion_id)
        path = root / "ingestion.json"
        if (
            root.is_symlink()
            or path.is_symlink()
            or not root.is_dir()
            or not path.is_file()
        ):
            raise FileNotFoundError(f"frozen ingestion not found: {ingestion_id}")
        payload = self._read_json(path)
        if (
            payload.get("schema_version")
            == FROZEN_SEMANTIC_INGESTION_SNAPSHOT_SCHEMA_VERSION
        ):
            snapshot = FrozenSemanticIngestionSnapshotV2.from_dict(
                payload
            )
            self._validate_semantic_ingestion_artifacts(root, snapshot)
            if snapshot.ingestion_id != ingestion_id:
                raise ValueError(
                    "ingestion artifact identity does not match its path"
                )
            return snapshot
        snapshot = FrozenIngestionSnapshot.from_dict(payload)
        validate_frozen_snapshot_quality(snapshot)
        if snapshot.ingestion_id != ingestion_id:
            raise ValueError("ingestion artifact identity does not match its path")
        if any(
            case.source.ingestion_id != snapshot.ingestion_id
            for case in snapshot.normalized_cases
        ):
            raise ValueError(
                "normalized case provenance does not match ingestion identity"
            )
        return snapshot

    def _write_semantic_ingestion(
        self,
        snapshot: FrozenSemanticIngestionSnapshotV2,
        *,
        dataset_recipe: DatasetRecipe | None,
    ) -> Path:
        if dataset_recipe is not None:
            frozen_trainable_ids = {
                case_id
                for case_id, split in (
                    snapshot.improvement_signal_set.case_splits.items()
                )
                if split.value in {"train", "validation"}
            }
            frozen_held_out_ids = {
                case_id
                for case_id, split in (
                    snapshot.improvement_signal_set.case_splits.items()
                )
                if split.value == "held_out"
            }
            if (
                set(dataset_recipe.trainable_case_ids)
                != frozen_trainable_ids
                or set(dataset_recipe.held_out_case_ids)
                != frozen_held_out_ids
            ):
                raise ValueError(
                    "dataset recipe differs from frozen semantic splits"
                )
        destination = self.ingestion_path(snapshot.ingestion_id)
        expected = snapshot.to_dict(public=False)
        if destination.exists():
            if destination.is_symlink() or not destination.is_dir():
                raise ValueError("ingestion artifact destination is unsafe")
            existing = self.read_ingestion(snapshot.ingestion_id)
            if not isinstance(
                existing,
                FrozenSemanticIngestionSnapshotV2,
            ) or existing.to_dict(public=False) != expected:
                raise ValueError(
                    "immutable ingestion id already exists with different content"
                )
            return destination

        root = destination.parent
        self._reject_symlink_components(root)
        root.mkdir(parents=True, exist_ok=True)
        root.chmod(0o700)
        temporary = root / (
            f".{snapshot.ingestion_id}.{uuid.uuid4().hex}.tmp"
        )
        temporary.mkdir(mode=0o700)
        try:
            self._write_private_json(
                temporary / "ingestion.json",
                expected,
            )
            self._write_private_json(
                temporary / "source_inventory.json",
                snapshot.inventory.to_dict(public=False),
            )
            self._write_private_json(
                temporary / "source_bundle.json",
                snapshot.source_bundle.to_dict(),
            )
            self._write_private_json(
                temporary / "constitution.json",
                snapshot.constitution.to_dict(),
            )
            self._write_private_json(
                temporary / "rollout_policy.json",
                snapshot.rollout_policy.to_dict(),
            )
            self._write_private_json(
                temporary / "semantic_profile.json",
                snapshot.semantic_profile.to_dict(),
            )
            self._write_private_json(
                temporary / "stage_reports.json",
                {"reports": [
                    item.to_dict() for item in snapshot.stage_reports
                ]},
            )
            self._write_private_json(
                temporary / "evidence_graph.json",
                snapshot.evidence_graph.to_dict(),
            )
            self._write_private_json(
                temporary / "evidence_authority_context.json",
                snapshot.evidence_authority_context.to_dict(),
            )
            self._write_private_json(
                temporary / "semantic_cases.json",
                {"cases": [
                    item.to_dict() for item in snapshot.semantic_cases
                ]},
            )
            self._write_private_json(
                temporary / "improvement_signals.json",
                snapshot.improvement_signal_set.to_dict(),
            )
            self._write_private_json(
                temporary / "target_evidence_bundle.json",
                snapshot.compiled_dataset.target_evidence_bundle.to_dict(),
            )
            self._write_private_json(
                temporary / "evaluation_plans.json",
                {"plans": [
                    item.to_dict()
                    for item in snapshot.evaluation_plans
                ]},
            )
            self._write_private_json(
                temporary / "resolved_traces.json",
                {"traces": [
                    item.to_dict()
                    for item in snapshot.resolved_traces
                ]},
            )
            self._write_private_json(
                temporary / "compiled_dataset.json",
                snapshot.compiled_dataset.to_dict(),
            )
            self._write_private_json(
                temporary / "quality_report.json",
                snapshot.quality_report.to_dict(),
            )
            self._write_private_json(
                temporary / "quality_gate.json",
                snapshot.quality_gate.to_dict(),
            )
            self._write_private_json(
                temporary / "resolution_evidence.json",
                snapshot.resolution_evidence.to_dict(),
            )
            self._write_private_json(
                temporary / "authority_registry.json",
                {
                    "authoritative_verification_ids": list(
                        snapshot.authoritative_verification_ids
                    ),
                    "verification_registry_fingerprint": (
                        snapshot.verification_registry_fingerprint
                    ),
                },
            )
            self._write_private_json(
                temporary / "qualification_registry.json",
                snapshot.qualification_registry.to_dict(),
            )
            if snapshot.qualification_report is not None:
                self._write_private_json(
                    temporary / "qualification_report.json",
                    snapshot.qualification_report.to_dict(),
                )
            if snapshot.source_manifest is not None:
                self._write_private_json(
                    temporary / "source_manifest.json",
                    snapshot.source_manifest,
                )
            approval_template = _semantic_evidence_approval_template(
                snapshot
            )
            if approval_template is not None:
                self._write_private_json(
                    temporary / "evidence_approval_template.json",
                    approval_template,
                )
            self._write_semantic_case_splits(
                temporary,
                snapshot,
                dataset_recipe=dataset_recipe,
            )
            if dataset_recipe is not None:
                self._write_private_json(
                    temporary / "dataset_recipe.json",
                    dataset_recipe,
                )
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        reloaded = self.read_ingestion(snapshot.ingestion_id)
        if (
            not isinstance(
                reloaded,
                FrozenSemanticIngestionSnapshotV2,
            )
            or reloaded.to_dict(public=False) != expected
        ):
            raise ValueError(
                "persisted semantic ingestion snapshot did not round trip"
            )
        return destination

    def _write_semantic_case_splits(
        self,
        root: Path,
        snapshot: FrozenSemanticIngestionSnapshotV2,
        *,
        dataset_recipe: DatasetRecipe | None,
    ) -> None:
        if dataset_recipe is None:
            trainable_ids = {
                case_id
                for case_id, split in (
                    snapshot.improvement_signal_set.case_splits.items()
                )
                if split.value in {"train", "validation"}
            }
            held_out_ids = {
                case_id
                for case_id, split in (
                    snapshot.improvement_signal_set.case_splits.items()
                )
                if split.value == "held_out"
            }
        else:
            trainable_ids = set(dataset_recipe.trainable_case_ids)
            held_out_ids = set(dataset_recipe.held_out_case_ids)
        self._write_private_jsonl(
            root / "trainable_cases.jsonl",
            tuple(
                item.to_dict()
                for item in snapshot.normalized_cases
                if item.case_id in trainable_ids
            ),
        )
        self._write_private_jsonl(
            root / "held_out_cases.jsonl",
            tuple(
                item.to_dict()
                for item in snapshot.normalized_cases
                if item.case_id in held_out_ids
            ),
        )

    def _validate_semantic_ingestion_artifacts(
        self,
        root: Path,
        snapshot: FrozenSemanticIngestionSnapshotV2,
    ) -> None:
        expected = {
            "source_inventory.json": (
                snapshot.inventory.to_dict(public=False)
            ),
            "source_bundle.json": snapshot.source_bundle.to_dict(),
            "constitution.json": snapshot.constitution.to_dict(),
            "rollout_policy.json": snapshot.rollout_policy.to_dict(),
            "semantic_profile.json": snapshot.semantic_profile.to_dict(),
            "stage_reports.json": {
                "reports": [
                    item.to_dict() for item in snapshot.stage_reports
                ]
            },
            "evidence_graph.json": snapshot.evidence_graph.to_dict(),
            "evidence_authority_context.json": (
                snapshot.evidence_authority_context.to_dict()
            ),
            "semantic_cases.json": {
                "cases": [
                    item.to_dict() for item in snapshot.semantic_cases
                ]
            },
            "improvement_signals.json": (
                snapshot.improvement_signal_set.to_dict()
            ),
            "target_evidence_bundle.json": (
                snapshot.compiled_dataset.target_evidence_bundle.to_dict()
            ),
            "evaluation_plans.json": {
                "plans": [
                    item.to_dict()
                    for item in snapshot.evaluation_plans
                ]
            },
            "resolved_traces.json": {
                "traces": [
                    item.to_dict()
                    for item in snapshot.resolved_traces
                ]
            },
            "compiled_dataset.json": (
                snapshot.compiled_dataset.to_dict()
            ),
            "quality_report.json": snapshot.quality_report.to_dict(),
            "quality_gate.json": snapshot.quality_gate.to_dict(),
            "resolution_evidence.json": (
                snapshot.resolution_evidence.to_dict()
            ),
            "authority_registry.json": {
                "authoritative_verification_ids": list(
                    snapshot.authoritative_verification_ids
                ),
                "verification_registry_fingerprint": (
                    snapshot.verification_registry_fingerprint
                ),
            },
            "qualification_registry.json": (
                snapshot.qualification_registry.to_dict()
            ),
        }
        if snapshot.qualification_report is not None:
            expected["qualification_report.json"] = (
                snapshot.qualification_report.to_dict()
            )
        if snapshot.source_manifest is not None:
            expected["source_manifest.json"] = dict(
                snapshot.source_manifest
            )
        approval_template = _semantic_evidence_approval_template(snapshot)
        if approval_template is not None:
            expected["evidence_approval_template.json"] = (
                approval_template
            )
        for relative_path, value in expected.items():
            path = root / relative_path
            if path.is_symlink() or not path.is_file():
                raise ValueError(
                    f"semantic ingestion artifact is missing: {relative_path}"
                )
            if self._read_json(path) != value:
                raise ValueError(
                    "semantic ingestion artifact differs from its frozen "
                    f"snapshot: {relative_path}"
                )
        trainable_ids = {
            case_id
            for case_id, split in (
                snapshot.improvement_signal_set.case_splits.items()
            )
            if split.value in {"train", "validation"}
        }
        held_out_ids = {
            case_id
            for case_id, split in (
                snapshot.improvement_signal_set.case_splits.items()
            )
            if split.value == "held_out"
        }
        expected_jsonl = {
            "trainable_cases.jsonl": [
                item.to_dict()
                for item in snapshot.normalized_cases
                if item.case_id in trainable_ids
            ],
            "held_out_cases.jsonl": [
                item.to_dict()
                for item in snapshot.normalized_cases
                if item.case_id in held_out_ids
            ],
        }
        for relative_path, records in expected_jsonl.items():
            path = root / relative_path
            if path.is_symlink() or not path.is_file():
                raise ValueError(
                    f"semantic ingestion artifact is missing: {relative_path}"
                )
            actual = [
                json.loads(line)
                for line in path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line
            ]
            if actual != records:
                raise ValueError(
                    "semantic ingestion case split differs from its frozen "
                    f"snapshot: {relative_path}"
                )

    def write_ingestion_ref(
        self,
        run_id: str,
        snapshot: FrozenIngestionSnapshot | FrozenSemanticIngestionSnapshotV2,
        *,
        dataset_recipe: DatasetRecipe | None = None,
    ) -> Path:
        if not isinstance(
            snapshot,
            (FrozenIngestionSnapshot, FrozenSemanticIngestionSnapshotV2),
        ):
            raise TypeError("ingestion snapshot must be typed")
        source = dict(dataset_recipe.source) if dataset_recipe is not None else {}
        split_fingerprint = source.get("split_fingerprint")
        if split_fingerprint is None and dataset_recipe is not None:
            split_fingerprint = fingerprint_json(dataset_recipe.splits)
        semantic = isinstance(
            snapshot,
            FrozenSemanticIngestionSnapshotV2,
        )
        payload = {
            "schema_version": (
                "aworld.self_evolve.ingestion_ref.v2"
                if semantic
                else "aworld.self_evolve.ingestion_ref.v1"
            ),
            "ingestion_id": snapshot.ingestion_id,
            "source_fingerprint": snapshot.inventory.source_root_fingerprint,
            "normalization_kind": (
                "semantic_evidence"
                if semantic
                else "structural_mapping"
            ),
            "mapping_fingerprint": (
                None
                if semantic
                else snapshot.selected_mapping.fingerprint
            ),
            "normalization_fingerprint": (
                snapshot.compiled_dataset.normalization_fingerprint
                if semantic
                else None
            ),
            "normalized_dataset_fingerprint": (
                snapshot.normalized_dataset_fingerprint
            ),
            "split_fingerprint": split_fingerprint,
            "quality_report_ref": str(
                self.ingestion_path(snapshot.ingestion_id) / "quality_report.json"
            ),
        }
        if semantic:
            payload.update(
                {
                    "evidence_graph_logical_fingerprint": (
                        snapshot.evidence_graph.logical_fingerprint
                    ),
                    "evidence_graph_provenance_fingerprint": (
                        snapshot.evidence_graph.provenance_fingerprint
                    ),
                    "improvement_signal_set_fingerprint": (
                        snapshot.improvement_signal_set.fingerprint
                    ),
                    "evaluation_plan_bundle_fingerprint": (
                        snapshot.compiled_dataset
                        .evaluation_plan_bundle_fingerprint
                    ),
                    "target_evidence_bundle_fingerprint": (
                        snapshot.compiled_dataset
                        .target_evidence_bundle.fingerprint
                    ),
                    "manifest_origin": snapshot.manifest_origin.value,
                }
            )
        path = self.run_path(run_id) / "ingestion_ref.json"
        self._write_json_atomic(path, payload)
        return path

    def read_ingestion_ref(self, run_id: str) -> dict[str, Any]:
        path = self.run_path(run_id) / "ingestion_ref.json"
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(f"ingestion reference not found for run: {run_id}")
        payload = self._read_json(path)
        if payload.get("schema_version") not in {
            "aworld.self_evolve.ingestion_ref.v1",
            "aworld.self_evolve.ingestion_ref.v2",
        }:
            raise ValueError("unsupported ingestion reference schema")
        ingestion_id = payload.get("ingestion_id")
        if not isinstance(ingestion_id, str):
            raise ValueError("ingestion reference is missing ingestion_id")
        snapshot = self.read_ingestion(ingestion_id)
        semantic = isinstance(
            snapshot,
            FrozenSemanticIngestionSnapshotV2,
        )
        expected: dict[str, Any] = {
            "source_fingerprint": snapshot.inventory.source_root_fingerprint,
            "mapping_fingerprint": (
                None
                if semantic
                else snapshot.selected_mapping.fingerprint
            ),
            "normalized_dataset_fingerprint": (
                snapshot.normalized_dataset_fingerprint
            ),
        }
        if semantic:
            expected.update(
                {
                    "normalization_kind": "semantic_evidence",
                    "normalization_fingerprint": (
                        snapshot.compiled_dataset.normalization_fingerprint
                    ),
                    "evidence_graph_logical_fingerprint": (
                        snapshot.evidence_graph.logical_fingerprint
                    ),
                    "evidence_graph_provenance_fingerprint": (
                        snapshot.evidence_graph.provenance_fingerprint
                    ),
                    "improvement_signal_set_fingerprint": (
                        snapshot.improvement_signal_set.fingerprint
                    ),
                    "evaluation_plan_bundle_fingerprint": (
                        snapshot.compiled_dataset
                        .evaluation_plan_bundle_fingerprint
                    ),
                    "target_evidence_bundle_fingerprint": (
                        snapshot.compiled_dataset
                        .target_evidence_bundle.fingerprint
                    ),
                    "manifest_origin": snapshot.manifest_origin.value,
                }
            )
        for key, value in expected.items():
            if payload.get(key) != value:
                raise ValueError(f"ingestion reference {key} does not match snapshot")
        return payload

    def write_campaign(self, campaign: Any) -> Path:
        from aworld.self_evolve.campaign import SelfImprovementCampaign

        if not isinstance(campaign, SelfImprovementCampaign):
            raise TypeError("campaign must be typed")
        path = self.campaign_path(campaign.campaign_id) / "campaign.json"
        self._write_json_atomic(path, campaign.to_dict())
        reloaded = self.read_campaign(campaign.campaign_id)
        if reloaded.to_dict() != campaign.to_dict():
            raise ValueError("persisted campaign checkpoint did not round trip")
        return path

    def read_campaign(self, campaign_id: str) -> Any:
        from aworld.self_evolve.campaign import (
            SelfImprovementCampaign,
            validate_campaign_source_snapshot,
        )
        from aworld.self_evolve.dataset_snapshot import (
            load_campaign_dataset_snapshot_manifest,
        )

        path = self.campaign_path(campaign_id) / "campaign.json"
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(f"self-improvement campaign not found: {campaign_id}")
        campaign = SelfImprovementCampaign.from_dict(self._read_json(path))
        dataset_snapshot_path = (
            self.campaign_path(campaign_id) / "dataset_snapshot"
        )
        if dataset_snapshot_path.exists():
            load_campaign_dataset_snapshot_manifest(
                dataset_snapshot_path,
                expected_campaign_id=campaign.campaign_id,
                expected_campaign_source_fingerprint=campaign.source_fingerprint,
            )
        else:
            validate_campaign_source_snapshot(
                campaign,
                workspace_root=self.workspace_root,
            )
        for run_id in campaign.run_ids:
            report = self.run_path(run_id) / "report.json"
            if not report.is_file() or report.is_symlink():
                raise ValueError(
                    f"campaign {campaign_id} references missing run {run_id}"
                )
        if campaign.status.value == "complete" and campaign.run_ids:
            latest = self.read_report(campaign.run_ids[-1])
            if latest.get("status") != "succeeded":
                raise ValueError("complete campaign must reference a succeeded run")
        return campaign

    def write_campaign_goal_handoff(
        self,
        campaign_id: str,
        payload: Mapping[str, Any],
    ) -> Path:
        path = self.campaign_path(campaign_id) / "goal_handoff.json"
        if payload.get("campaign_id") != campaign_id:
            raise ValueError("goal handoff does not match its campaign")
        self._write_json_atomic(path, dict(payload))
        return path

    def read_campaign_goal_handoff(self, campaign_id: str) -> dict[str, Any]:
        path = self.campaign_path(campaign_id) / "goal_handoff.json"
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(f"campaign goal handoff not found: {campaign_id}")
        payload = self._read_json(path)
        if payload.get("campaign_id") != campaign_id:
            raise ValueError("goal handoff does not match its campaign")
        return payload

    def read_report(self, run_id: str) -> dict[str, Any]:
        path = self.run_path(run_id) / "report.json"
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(f"self-evolve report not found: {run_id}")
        return self._read_json(path)

    def archive_interrupted_campaign_run(
        self,
        *,
        campaign_id: str,
        run_id: str,
        reserved_usage: Mapping[str, Any],
    ) -> Path:
        """Atomically preserve a dead, incomplete Campaign run before retry."""

        self._validate_id(campaign_id, "campaign_id")
        self._validate_id(run_id, "run_id")
        if not run_id.startswith(f"{campaign_id}-cycle-"):
            raise ValueError("interrupted run does not belong to its campaign")
        run_dir = self.run_path(run_id)
        if not run_dir.is_dir() or run_dir.is_symlink():
            raise FileNotFoundError(f"incomplete self-evolve run not found: {run_id}")
        if (run_dir / "report.json").exists():
            raise ValueError("completed self-evolve run cannot be archived as interrupted")
        lease_path = run_dir / ".active.json"
        lease = self._read_json(lease_path) if lease_path.is_file() else {}
        if _run_lease_is_live(lease):
            raise RuntimeError(f"self-evolve run is still active: {run_id}")
        for journal_path in (run_dir / "apply").glob("*.journal.json"):
            journal = self._read_json(journal_path)
            if journal.get("status") in {"backup_written", "applying"}:
                raise RuntimeError(
                    f"self-evolve run has an interrupted apply journal: {run_id}"
                )

        archive_root = (
            self.campaign_path(campaign_id) / "interrupted_run_attempts"
        )
        archive_root.mkdir(parents=True, exist_ok=True)
        attempt_index = 1
        while True:
            archive_path = archive_root / f"{run_id}-attempt-{attempt_index:03d}"
            if not archive_path.exists():
                break
            attempt_index += 1
        os.replace(run_dir, archive_path)
        self._write_json(
            archive_path / "interruption.json",
            {
                "schema_version": "aworld.self_evolve.interrupted_run.v1",
                "code": "campaign_run_interrupted",
                "campaign_id": campaign_id,
                "run_id": run_id,
                "attempt_index": attempt_index,
                "archived_at": time.time(),
                "lease": public_diagnostic_projection(lease),
                "reserved_usage": public_diagnostic_projection(reserved_usage),
            },
        )
        return archive_path

    def create_run(self, run: SelfEvolveRun) -> Path:
        run_dir = self.run_path(run.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        run_payload = to_json_dict(run)
        raw_gates = run_payload.get("gate_results")
        if isinstance(raw_gates, list):
            for gate in raw_gates:
                if isinstance(gate, dict):
                    if "reason" in gate:
                        gate["reason"] = public_diagnostic_projection(
                            gate.get("reason")
                        )
                    if "details" in gate:
                        gate["details"] = public_diagnostic_projection(
                            gate.get("details")
                        )
        raw_metrics = run_payload.get("metrics")
        if isinstance(raw_metrics, list):
            for metric in raw_metrics:
                if isinstance(metric, dict) and "metrics" in metric:
                    metric["metrics"] = public_diagnostic_projection(
                        metric.get("metrics")
                    )
        self._write_json(run_dir / "run.json", run_payload)
        active_lease = run_dir / ".active.json"
        if run.status == SelfEvolveRunStatus.RUNNING:
            self._write_json(
                active_lease,
                {
                    "hostname": socket.gethostname(),
                    "pid": os.getpid(),
                    "started_at": time.time(),
                },
            )
        else:
            active_lease.unlink(missing_ok=True)
        return run_dir

    def write_candidate(self, run_id: str, candidate: CandidateVariant) -> Path:
        self._validate_id(candidate.candidate_id, "candidate_id")
        candidate_dir = self.run_path(run_id) / "candidates"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        content_path = candidate_dir / f"{candidate.candidate_id}.md"
        content = candidate.content
        if candidate.target.target_type == "skill":
            content = mark_skill_content_candidate(
                candidate.content,
                run_id=run_id,
                candidate_id=candidate.candidate_id,
            )
        content_path.write_text(content, encoding="utf-8")
        self._write_json(content_path.with_suffix(".json"), candidate)
        if candidate.target.target_type == "skill":
            package_dir = candidate_dir / candidate.candidate_id
            if package_dir.is_symlink() or package_dir.is_file():
                package_dir.unlink()
            elif package_dir.exists():
                shutil.rmtree(package_dir)
            package_dir.mkdir()
            (package_dir / "SKILL.md").write_text(content, encoding="utf-8")
            for item in validate_candidate_files(candidate.files):
                if item.operation != "upsert":
                    continue
                destination = package_dir.joinpath(*Path(item.path).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(item.content or "", encoding="utf-8")
                mode = destination.stat().st_mode
                destination.chmod(
                    (mode | 0o111) if item.executable else (mode & ~0o111)
                )
            self._write_json(package_dir / "candidate.json", candidate)
        return content_path

    def write_report(self, run_id: str, report: Mapping[str, Any]) -> Path:
        path = self.run_path(run_id) / "report.json"
        payload = dict(report)
        ingestion_ref_path = self.run_path(run_id) / "ingestion_ref.json"
        if ingestion_ref_path.is_file() and not ingestion_ref_path.is_symlink():
            ingestion_ref = self.read_ingestion_ref(run_id)
            snapshot = self.read_ingestion(str(ingestion_ref["ingestion_id"]))
            if isinstance(
                snapshot,
                FrozenSemanticIngestionSnapshotV2,
            ):
                payload["ingestion"] = {
                    **snapshot.public_projection(),
                    "split_fingerprint": ingestion_ref.get(
                        "split_fingerprint"
                    ),
                }
            else:
                payload["ingestion"] = {
                    "schema_version": snapshot.schema_version,
                    "ingestion_id": snapshot.ingestion_id,
                    "source_fingerprint": snapshot.inventory.source_root_fingerprint,
                    "mapping_fingerprint": snapshot.selected_mapping.fingerprint,
                    "normalized_dataset_fingerprint": (
                        snapshot.normalized_dataset_fingerprint
                    ),
                    "split_fingerprint": ingestion_ref.get("split_fingerprint"),
                    "ingestor_name": snapshot.ingestor_name,
                    "ingestor_version": snapshot.ingestor_version,
                    "ingestor_trust_level": snapshot.ingestor_trust_level.value,
                    "quality_report": snapshot.quality_report.public_projection(),
                }
        ingestion_gate_path = self.run_path(run_id) / "ingestion_gate.json"
        if ingestion_gate_path.is_file() and not ingestion_gate_path.is_symlink():
            ingestion_gate = self._read_json(ingestion_gate_path)
            gates = [
                item
                for item in payload.get("gate_results", ())
                if isinstance(item, Mapping)
                and item.get("gate_name") != "dataset_ingestion"
            ]
            payload["gate_results"] = [ingestion_gate, *gates]
        self._write_json(path, _public_report_payload(payload))
        return path

    def write_ingestion_gate(
        self,
        run_id: str,
        gate: Mapping[str, Any],
    ) -> Path:
        if gate.get("gate_name") != "dataset_ingestion":
            raise ValueError("ingestion gate must use dataset_ingestion")
        path = self.run_path(run_id) / "ingestion_gate.json"
        self._write_json_atomic(path, dict(gate))
        return path

    def write_dataset_recipe(self, run_id: str, recipe: DatasetRecipe) -> Path:
        path = self.run_path(run_id) / "dataset_recipe.json"
        self._write_json(path, recipe)
        source = recipe.source
        ingestion_id = source.get("ingestion_id")
        if source.get("kind") == "agentic_source" and isinstance(
            ingestion_id, str
        ):
            self.write_ingestion_ref(
                run_id,
                self.read_ingestion(ingestion_id),
                dataset_recipe=recipe,
            )
        return path

    def write_replay_requirements(
        self,
        run_id: str,
        report: ReplayPreflightReport,
    ) -> Path:
        path = self.run_path(run_id) / "replay_requirements.json"
        self._write_json(path, report)
        return path

    def write_regression_evidence(
        self,
        run_id: str,
        evidence: RegressionEvidence,
    ) -> Path:
        if not isinstance(evidence, RegressionEvidence):
            raise TypeError("regression evidence must be typed")
        self._validate_id(evidence.candidate_id, "candidate_id")
        path = (
            self.run_path(run_id)
            / "regression"
            / "evidence"
            / f"{evidence.candidate_id}.json"
        )
        self._write_json(path, evidence.to_dict())
        return path

    def write_regression_suite_manifest(
        self,
        run_id: str,
        suites: tuple[RegressionSuiteSpec, ...],
    ) -> Path:
        if any(not isinstance(suite, RegressionSuiteSpec) for suite in suites):
            raise TypeError("regression suite manifest requires typed suites")
        path = self.run_path(run_id) / "regression" / "suites.json"
        self._write_json(
            path,
            {
                "schema_version": "aworld.self_evolve.regression_suite_manifest.v1",
                "suite_count": len(suites),
                "suites": [suite.to_dict() for suite in suites],
            },
        )
        return path

    def write_challenge_report(
        self,
        run_id: str,
        candidate_id: str,
        report: ChallengeReport | Mapping[str, Any],
    ) -> Path:
        self._validate_id(candidate_id, "candidate_id")
        if isinstance(report, ChallengeReport):
            payload = report.to_dict()
        elif isinstance(report, Mapping):
            payload = dict(report)
        else:
            raise TypeError("challenge report must be typed or a diagnostic mapping")
        path = (
            self.run_path(run_id)
            / "regression"
            / "challenger"
            / f"{candidate_id}.json"
        )
        self._write_json(path, payload)
        return path

    def write_handbook_slice(
        self,
        run_id: str,
        iteration: int,
        payload: Mapping[str, Any],
    ) -> Path:
        if isinstance(iteration, bool) or iteration <= 0:
            raise ValueError("handbook iteration must be positive")
        path = (
            self.run_path(run_id)
            / "handbook"
            / f"iteration-{iteration:03d}.json"
        )
        self._write_json(path, payload)
        return path

    def write_replay_evidence_reuse(
        self,
        run_id: str,
        candidate_id: str,
        report: Mapping[str, Any],
    ) -> Path:
        """Persist provenance for replay evidence reused without execution."""

        self._validate_id(candidate_id, "candidate_id")
        path = (
            self.run_path(run_id)
            / "replay_evidence_reuse"
            / f"{candidate_id}.json"
        )
        self._write_json(path, report)
        return path

    def write_target_provenance(self, run_id: str, provenance: TargetProvenance) -> Path:
        path = self.run_path(run_id) / "target_provenance.json"
        self._write_json(path, provenance)
        return path

    def write_target_selection_report(
        self,
        run_id: str,
        report: TargetSelectionReport,
    ) -> Path:
        path = self.run_path(run_id) / "target_selection.json"
        self._write_json(path, report)
        return path

    def write_optimizer_lineage(self, run_id: str, lineage: OptimizerLineage) -> Path:
        self._validate_id(lineage.candidate_id, "candidate_id")
        lineage_dir = self.run_path(run_id) / "optimizer_lineage"
        lineage_dir.mkdir(parents=True, exist_ok=True)
        path = lineage_dir / f"{lineage.candidate_id}.json"
        self._write_json(path, lineage)
        return path

    def candidate_attempt_path(self, key: CandidateAttemptKey) -> Path:
        """Return the append-only lifecycle stream path for one generation slot."""

        if not isinstance(key, CandidateAttemptKey):
            raise TypeError("candidate attempt key must be typed")
        self._validate_id(key.run_id, "run_id")
        run_root = self.run_path(key.run_id)
        path = (
            run_root
            / "candidate_attempts"
            / f"iteration-{key.iteration:08d}"
            / f"slot-{key.slot:08d}"
            / "events.jsonl"
        )
        if not path.resolve().is_relative_to(run_root.resolve()):
            raise ValueError("candidate attempt path escapes its run directory")
        return path

    def append_candidate_attempt_event(
        self,
        event: CandidateAttemptEvent,
    ) -> Path:
        """Atomically append one event without exposing a partial JSON record."""

        if not isinstance(event, CandidateAttemptEvent):
            raise TypeError("candidate attempt event must be typed")
        path = self.candidate_attempt_path(event.key)
        if path.is_symlink():
            raise ValueError("candidate attempt event stream cannot be a symlink")
        existing = self.read_candidate_attempt_events(event.key)
        validate_candidate_attempt_lifecycle((*existing, event))
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.parent.is_symlink():
            raise ValueError("candidate attempt directory cannot be a symlink")
        encoded_events = [
            json.dumps(
                item.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for item in (*existing, event)
        ]
        payload = ("\n".join(encoded_events) + "\n").encode("utf-8")
        temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        try:
            # Write the next complete logical stream away from the canonical
            # path. A short write, ENOSPC, flush, or fsync failure therefore
            # leaves the previously committed stream readable.
            with temporary.open("xb") as stream:
                offset = 0
                while offset < len(payload):
                    written = stream.write(memoryview(payload)[offset:])
                    if (
                        not isinstance(written, int)
                        or isinstance(written, bool)
                        or written <= 0
                        or written > len(payload) - offset
                    ):
                        raise OSError(
                            "candidate attempt stream write made invalid progress"
                        )
                    offset += written
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Cleanup must never replace the append/fsync/rename error.
                # Orphaned temp files are not part of the canonical stream.
                pass
        return path

    def write_candidate_attempt_event(
        self,
        event: CandidateAttemptEvent,
    ) -> Path:
        """Compatibility spelling for the explicitly append-only operation."""

        return self.append_candidate_attempt_event(event)

    def read_candidate_attempt_events(
        self,
        key: CandidateAttemptKey,
    ) -> tuple[CandidateAttemptEvent, ...]:
        path = self.candidate_attempt_path(key)
        if not path.exists():
            return ()
        if path.is_symlink() or not path.is_file():
            raise ValueError("candidate attempt event stream must be a regular file")
        events: list[CandidateAttemptEvent] = []
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                raise ValueError(
                    f"candidate attempt event stream has an empty line: {line_number}"
                )
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"candidate attempt event is invalid JSON: {line_number}"
                ) from exc
            if not isinstance(payload, Mapping):
                raise ValueError("candidate attempt event must be a JSON object")
            event = CandidateAttemptEvent.from_dict(payload)
            if event.key != key:
                raise ValueError("candidate attempt event path/key mismatch")
            events.append(event)
        if events:
            validate_candidate_attempt_lifecycle(events)
        return tuple(events)

    def read_all_candidate_attempt_events(
        self,
        run_id: str,
    ) -> tuple[CandidateAttemptEvent, ...]:
        root = self.run_path(run_id) / "candidate_attempts"
        if not root.exists():
            return ()
        if root.is_symlink() or not root.is_dir():
            raise ValueError("candidate attempt root must be a regular directory")
        events: list[CandidateAttemptEvent] = []
        for path in sorted(root.glob("iteration-*/slot-*/events.jsonl")):
            if path.is_symlink() or not path.is_file():
                raise ValueError("candidate attempt event stream must be a regular file")
            for line in path.read_text(encoding="utf-8").splitlines():
                payload = json.loads(line)
                if not isinstance(payload, Mapping):
                    raise ValueError("candidate attempt event must be a JSON object")
                event = CandidateAttemptEvent.from_dict(payload)
                if event.key.run_id != run_id:
                    raise ValueError("candidate attempt event belongs to another run")
                if self.candidate_attempt_path(event.key) != path:
                    raise ValueError("candidate attempt event path/key mismatch")
                events.append(event)
        grouped: dict[CandidateAttemptKey, list[CandidateAttemptEvent]] = {}
        for event in events:
            grouped.setdefault(event.key, []).append(event)
        for values in grouped.values():
            validate_candidate_attempt_lifecycle(values)
        return tuple(
            sorted(events, key=lambda item: (item.key, item.sequence))
        )

    def write_lesson_records(self, run_id: str, lessons: tuple[Any, ...]) -> Path:
        from aworld.self_evolve.lessons import (
            LessonRecord,
            aggregate_lesson_records,
            validate_lesson_records,
        )

        lessons_dir = self.run_path(run_id) / "lessons"
        lessons_dir.mkdir(parents=True, exist_ok=True)
        path = lessons_dir / "lessons.jsonl"
        typed_lessons = tuple(
            lesson for lesson in lessons if isinstance(lesson, LessonRecord)
        )
        if len(typed_lessons) == len(lessons):
            validate_lesson_records(typed_lessons)
            lessons = aggregate_lesson_records(typed_lessons)
            validate_lesson_records(lessons)
        else:
            lesson_ids = [getattr(lesson, "lesson_id", None) for lesson in lessons]
            if len(lesson_ids) != len(set(lesson_ids)):
                raise ValueError("duplicate lesson ids require typed LessonRecord values")
        lines = [
            json.dumps(to_json_dict(lesson), ensure_ascii=False, sort_keys=True)
            for lesson in lessons
        ]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return path

    def write_harness_diagnostics(self, run_id: str, diagnostics: tuple[Any, ...]) -> Path:
        diagnostics_dir = self.run_path(run_id) / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        path = diagnostics_dir / "harness_diagnostics.jsonl"
        lines = [
            json.dumps(
                public_diagnostic_projection(to_json_dict(diagnostic)),
                ensure_ascii=False,
                sort_keys=True,
            )
            for diagnostic in diagnostics
        ]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return path

    def write_judge_record(self, run_id: str, record: JudgeRecord) -> Path:
        self._validate_id(record.backend_id, "backend_id")
        judge_dir = self.run_path(run_id) / "judges"
        judge_dir.mkdir(parents=True, exist_ok=True)
        path = judge_dir / f"{record.backend_id}.json"
        self._write_json(path, record)
        return path

    def write_apply_backup(
        self,
        run_id: str,
        *,
        candidate: CandidateVariant,
        original_content: str,
        target_path: str | None,
    ) -> tuple[Path, Path]:
        self._validate_id(candidate.candidate_id, "candidate_id")
        apply_dir = self.run_path(run_id) / "apply"
        apply_dir.mkdir(parents=True, exist_ok=True)
        backup_path = apply_dir / f"{candidate.candidate_id}.backup.md"
        backup_path.write_text(original_content, encoding="utf-8")
        journal_path = apply_dir / f"{candidate.candidate_id}.journal.json"
        package_backup_path: Path | None = None
        target_root: Path | None = None
        target_root_existed: bool | None = None
        package_backup_fingerprint: str | None = None
        if (
            candidate.target.target_type == "skill"
            and candidate.files
            and target_path is not None
        ):
            target_root = Path(target_path).parent
            target_root_existed = target_root.exists()
            package_backup_path = apply_dir / f"{candidate.candidate_id}.backup.skill"
            if package_backup_path.is_symlink() or package_backup_path.is_file():
                package_backup_path.unlink()
            elif package_backup_path.exists():
                shutil.rmtree(package_backup_path)
            if target_root_existed:
                shutil.copytree(target_root, package_backup_path, symlinks=True)
                package_backup_fingerprint = _directory_fingerprint(
                    package_backup_path
                )
        self._write_json(
            journal_path,
            {
                "candidate_id": candidate.candidate_id,
                "target": candidate.target,
                "target_path": target_path,
                "backup_path": str(backup_path),
                "package_backup_path": (
                    str(package_backup_path)
                    if package_backup_path is not None
                    else None
                ),
                "target_root": str(target_root) if target_root is not None else None,
                "target_root_existed": target_root_existed,
                "package_backup_fingerprint": package_backup_fingerprint,
                "candidate_package_fingerprint": candidate_package_fingerprint(
                    candidate
                ),
                "status": "backup_written",
            },
        )
        return backup_path, journal_path

    def update_apply_journal(
        self,
        journal_path: str | Path,
        *,
        status: str,
        details: Mapping[str, Any] | None = None,
    ) -> Path:
        path = Path(journal_path)
        payload = self._read_json(path)
        payload["status"] = status
        if details:
            payload.setdefault("details", {}).update(dict(details))
        self._write_json(path, payload)
        return path

    def recover_interrupted_apply(self, journal_path: str | Path) -> Mapping[str, Any]:
        path = Path(journal_path)
        payload = self._read_json(path)
        status = payload.get("status")
        if status not in {"backup_written", "applying"}:
            return {
                "status": "skipped",
                "reason": "apply journal is not in an interrupted state",
            }
        backup_path = Path(str(payload.get("backup_path") or ""))
        target_path = Path(str(payload.get("target_path") or ""))
        package_backup_value = payload.get("package_backup_path")
        if isinstance(package_backup_value, str) and package_backup_value:
            target_root = Path(str(payload.get("target_root") or target_path.parent))
            target_root_existed = payload.get("target_root_existed") is True
            package_backup_path = Path(package_backup_value)
            if target_root_existed and not package_backup_path.is_dir():
                return self._record_recovery_failure(
                    path,
                    payload,
                    reason="skill package backup is missing",
                )
            expected_backup_fingerprint = payload.get(
                "package_backup_fingerprint"
            )
            if (
                target_root_existed
                and isinstance(expected_backup_fingerprint, str)
                and _directory_fingerprint(package_backup_path)
                != expected_backup_fingerprint
            ):
                return self._record_recovery_failure(
                    path,
                    payload,
                    reason="skill package backup fingerprint mismatch",
                )
            if target_root_existed:
                target_root.parent.mkdir(parents=True, exist_ok=True)
                staging = target_root.parent / (
                    f".{target_root.name}.aworld-recovery-{uuid.uuid4().hex}"
                )
                try:
                    shutil.copytree(package_backup_path, staging, symlinks=True)
                    if target_root.exists() and target_root.is_dir() and not target_root.is_symlink():
                        atomic_exchange_paths(target_root, staging)
                        shutil.rmtree(staging)
                    elif target_root.exists() or target_root.is_symlink():
                        return self._record_recovery_failure(
                            path,
                            payload,
                            reason="skill package target is not a regular directory",
                        )
                    else:
                        staging.rename(target_root)
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
            elif target_root.exists() or target_root.is_symlink():
                trash = target_root.parent / (
                    f".{target_root.name}.aworld-trash-{uuid.uuid4().hex}"
                )
                target_root.rename(trash)
                if trash.is_symlink() or trash.is_file():
                    trash.unlink()
                else:
                    shutil.rmtree(trash)
            recovery = {
                "status": "recovered_rolled_back",
                "restored_from_backup": True,
                "target_path": str(target_path),
                "backup_path": str(package_backup_path),
                "package_restored": True,
            }
            payload["status"] = "recovered_rolled_back"
            payload["recovery"] = recovery
            self._write_json(path, payload)
            return recovery
        if not backup_path.exists() or not target_path.exists():
            return self._record_recovery_failure(
                path,
                payload,
                reason="backup or target path is missing",
            )

        target_path.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
        recovery = {
            "status": "recovered_rolled_back",
            "restored_from_backup": True,
            "target_path": str(target_path),
            "backup_path": str(backup_path),
        }
        payload["status"] = "recovered_rolled_back"
        payload["recovery"] = recovery
        self._write_json(path, payload)
        return recovery

    def _record_recovery_failure(
        self,
        journal_path: Path,
        payload: dict[str, Any],
        *,
        reason: str,
    ) -> Mapping[str, Any]:
        recovery = {
            "status": "recovery_failed",
            "restored_from_backup": False,
            "reason": reason,
        }
        payload["status"] = "recovery_failed"
        payload["recovery"] = recovery
        self._write_json(journal_path, payload)
        return recovery

    def _create_empty_private_file(self, path: Path) -> None:
        self._reject_symlink_components(path.parent)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._reject_symlink_components(path.parent)
        descriptor = os.open(
            path,
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @contextmanager
    def _measurement_control_append_lock(self, root: Path):
        if fcntl is None:
            raise RuntimeError(
                "safe measurement journal append requires filesystem locking"
            )
        self._reject_symlink_components(root)
        if root.is_symlink() or not root.is_dir():
            raise ValueError("measurement control plan destination is unsafe")
        lock_path = root / ".append.lock"
        if lock_path.is_symlink():
            raise ValueError("measurement control append lock cannot be a symlink")
        descriptor = os.open(
            lock_path,
            os.O_CREAT
            | os.O_RDWR
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read_measurement_control_journal_bytes(self, path: Path) -> bytes:
        self._reject_symlink_components(path.parent)
        if path.is_symlink() or not path.is_file():
            raise MeasurementControlCorruptionError(
                "measurement_journal_missing",
                "measurement control journal is missing or unsafe",
            )
        size = path.stat().st_size
        if size > _MEASUREMENT_JOURNAL_MAX_BYTES:
            raise MeasurementControlCorruptionError(
                "measurement_journal_oversized",
                "measurement journal exceeds its hard read bound",
            )
        with path.open("rb") as stream:
            payload = stream.read(_MEASUREMENT_JOURNAL_MAX_BYTES + 1)
        if len(payload) > _MEASUREMENT_JOURNAL_MAX_BYTES:
            raise MeasurementControlCorruptionError(
                "measurement_journal_oversized",
                "measurement journal grew beyond its hard read bound",
            )
        return payload

    def _measurement_control_journal_path(
        self, root: Path, index: MeasurementControlIndex
    ) -> Path:
        path = root / index.journal_file
        if path.parent != root:
            raise MeasurementControlCorruptionError(
                "measurement_journal_path_invalid",
                "measurement journal identity escaped its plan root",
            )
        return path

    def _measurement_control_journal_size(self, path: Path) -> int:
        self._reject_symlink_components(path.parent)
        if path.is_symlink() or not path.is_file():
            raise MeasurementControlCorruptionError(
                "measurement_journal_missing",
                "measurement control journal is missing or unsafe",
            )
        return path.stat().st_size

    def _record_oversized_measurement_journal(
        self,
        root: Path,
        *,
        journal_file: str,
        observed_bytes: int,
        confirmed_bytes: int,
    ) -> None:
        self._write_json_atomic(
            root / "quarantine" / "oversized-journal.json",
            {
                "schema_version": "aworld.measurement_journal_quarantine.v1",
                "journal_file": journal_file,
                "observed_bytes": observed_bytes,
                "confirmed_bytes": confirmed_bytes,
                "content_copied": False,
                "reason_code": "measurement_journal_oversized",
            },
        )

    def _read_bounded_json(self, path: Path, byte_limit: int) -> Mapping[str, Any]:
        self._reject_symlink_components(path.parent)
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(path)
        if path.stat().st_size > byte_limit:
            raise ValueError("bounded JSON artifact is oversized")
        with path.open("rb") as stream:
            raw = stream.read(byte_limit + 1)
        if len(raw) > byte_limit:
            raise ValueError("bounded JSON artifact grew beyond its limit")
        value = json.loads(raw)
        if not isinstance(value, Mapping):
            raise ValueError("bounded JSON artifact must be an object")
        return value

    def _decode_measurement_control_journal(
        self,
        plan: MeasurementPlanV2,
        journal_bytes: bytes,
    ) -> tuple[WorkUnitJournalEvent, ...]:
        if journal_bytes and not journal_bytes.endswith(b"\n"):
            raise MeasurementControlCorruptionError(
                "measurement_journal_torn_record",
                "measurement journal ends with an unconfirmed partial record",
            )
        result: list[WorkUnitJournalEvent] = []
        for line_number, raw_line in enumerate(journal_bytes.splitlines(), start=1):
            if not raw_line.strip():
                continue
            if len(raw_line) > 65_536:
                raise MeasurementControlCorruptionError(
                    "measurement_journal_record_oversized",
                    f"measurement journal line {line_number} exceeds 64 KiB",
                )
            try:
                payload = json.loads(raw_line)
                if not isinstance(payload, Mapping):
                    raise ValueError("journal record must be an object")
                event = WorkUnitJournalEvent.from_dict(payload)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise MeasurementControlCorruptionError(
                    "measurement_journal_record_invalid",
                    f"measurement journal line {line_number} is invalid",
                ) from exc
            if event.measurement_plan_fingerprint != (
                plan.measurement_plan_fingerprint
            ):
                raise MeasurementControlCorruptionError(
                    "measurement_journal_plan_identity_mismatch",
                    "measurement journal event references a different plan",
                )
            result.append(event)
        return tuple(result)

    def _decode_measurement_control_journal_records(
        self,
        plan: MeasurementPlanV2,
        journal_bytes: bytes,
    ) -> tuple[tuple[WorkUnitJournalEvent, bytes], ...]:
        if journal_bytes and not journal_bytes.endswith(b"\n"):
            raise MeasurementControlCorruptionError(
                "measurement_journal_torn_record",
                "journal tail contains an incomplete record",
            )
        result: list[tuple[WorkUnitJournalEvent, bytes]] = []
        for raw_line in journal_bytes.splitlines(keepends=True):
            if raw_line.strip():
                decoded = self._decode_measurement_control_journal(plan, raw_line)
                if len(decoded) != 1:
                    raise MeasurementControlCorruptionError(
                        "measurement_journal_record_invalid",
                        "journal record did not decode to one event",
                    )
                result.append((decoded[0], raw_line))
        return tuple(result)

    @staticmethod
    def _write_all(descriptor: int, encoded: bytes) -> None:
        view = memoryview(encoded)
        offset = 0
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise OSError("short journal write made no progress")
            offset += written

    def _write_bytes_atomic(self, path: Path, payload: bytes) -> None:
        self._reject_symlink_components(path.parent)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._reject_symlink_components(path.parent)
        if path.is_symlink():
            raise ValueError("atomic byte destination cannot be a symlink")
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            self._write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _parse_utc_datetime(value: str, field_name: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
        if parsed.tzinfo is None:
            raise ValueError(f"{field_name} must include a timezone")
        return parsed.astimezone(timezone.utc)

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(to_json_dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _write_json_atomic(self, path: Path, payload: Any) -> None:
        self._reject_symlink_components(path.parent)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._reject_symlink_components(path.parent)
        if path.is_symlink():
            raise ValueError("atomic JSON destination cannot be a symlink")
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        encoded = (
            json.dumps(to_json_dict(payload), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_private_json(self, path: Path, payload: Any) -> None:
        self._write_json(path, payload)
        path.chmod(0o600)

    def _write_private_jsonl(
        self,
        path: Path,
        records: tuple[Mapping[str, Any], ...],
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = "".join(
            json.dumps(
                to_json_dict(record),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for record in records
        )
        path.write_text(encoded, encoding="utf-8")
        path.chmod(0o600)

    @staticmethod
    def _reject_symlink_components(path: Path) -> None:
        for component in (path, *path.parents):
            if component.is_symlink():
                raise ValueError("atomic JSON destination cannot traverse a symlink")

    def _read_json(self, path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"expected JSON object in {path}")
        return payload

    def _validate_id(self, value: str, field_name: str) -> None:
        if not value or "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError(f"invalid {field_name}: {value!r}")


def _directory_fingerprint(root: Path) -> str:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append(
                {"path": relative, "kind": "symlink", "target": path.readlink().as_posix()}
            )
        elif path.is_file():
            content = path.read_bytes()
            entries.append(
                {
                    "path": relative,
                    "kind": "file",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                    "mode": path.stat().st_mode & 0o777,
                }
            )
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _run_lease_is_live(lease: Mapping[str, Any]) -> bool:
    hostname = lease.get("hostname")
    if not isinstance(hostname, str) or not hostname:
        # Absence of a valid ownership proof is not proof that the run died.
        return True
    if hostname != socket.gethostname():
        # A foreign-host lease cannot be probed safely.
        return True
    pid = lease.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (OSError, PermissionError):
        return True
    return True


_DYNAMIC_REPORT_FIELDS = frozenset(
    {
        "acceptance_confidence",
        "baseline_metrics",
        "candidate_metrics",
        "content_quality_diagnostics",
        "gate_results",
        "held_out_metrics",
        "measurement",
        "no_op",
        "optimizer_diagnostics",
        "population",
        "release_checklist",
        "stopping_condition",
        "terminal_cause",
    }
)


def _public_report_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    """Project dynamic report fields without truncating the top-level schema."""

    projected = dict(report)
    for key in _DYNAMIC_REPORT_FIELDS:
        if key in projected:
            projected[key] = public_diagnostic_projection(projected[key])
    raw_gates = report.get("gate_results")
    if isinstance(raw_gates, list):
        projected["gate_results"] = [
            {
                str(key): public_diagnostic_projection(value)
                for key, value in gate.items()
            }
            if isinstance(gate, Mapping)
            else public_diagnostic_projection(gate)
            for gate in raw_gates
        ]
    for section_name in ("post_apply", "release_normalization"):
        section = report.get(section_name)
        if isinstance(section, Mapping):
            projected[section_name] = {
                str(key): (
                    value
                    if str(key).endswith(("_path", "_paths"))
                    else public_diagnostic_projection(value)
                )
                for key, value in section.items()
            }
    replay = projected.get("replay")
    if isinstance(replay, Mapping):
        replay_payload = dict(replay)
        for variant_key in ("baseline", "candidate"):
            variant = replay_payload.get(variant_key)
            if isinstance(variant, Mapping):
                replay_payload[variant_key] = {
                    str(key): public_diagnostic_projection(value)
                    for key, value in variant.items()
                }
        members = replay_payload.get("members")
        if isinstance(members, list):
            replay_payload["members"] = [
                {
                    str(key): public_diagnostic_projection(value)
                    for key, value in member.items()
                }
                if isinstance(member, Mapping)
                else public_diagnostic_projection(member)
                for member in members
            ]
        projected["replay"] = replay_payload
    return projected
