from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from aworld.self_evolve.recovery_trace import (
    RECOVERY_TRACE_SCHEMA_VERSION,
    validate_public_recovery_trace,
)


CAMPAIGN_SCHEMA_VERSION = "aworld.self_evolve.campaign.v1"
DISPOSITION_SCHEMA_VERSION = "aworld.self_evolve.disposition.v1"
PROGRESS_SCHEMA_VERSION = "aworld.self_evolve.progress.v1"
DEFAULT_MAX_IMPROVEMENT_CYCLES = 3

_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,159}$")
_RUNTIME_ONLY_REQUEST_KEYS = {
    "candidate_replay_backend",
    "concurrency_policy",
    "evaluation_backend",
    "mutation_model_config",
    "post_apply_evaluator",
    "progress_callback",
    "replay_adaptation_compiler",
    "runtime_registry_refresher",
    "runtime_skill_activator",
}
_SOURCE_REQUEST_KEYS = {
    "batch_config",
    "current_trajectory",
    "dataset",
    "from_session",
    "from_trajectory",
    "from_trajectory_set",
}
_RESUME_CONFLICT_KEYS = {
    *_SOURCE_REQUEST_KEYS,
    "from_run",
    "target",
}
_STAGE_RANK = {
    "target_selection": 1,
    "candidate_generation": 2,
    "candidate_repair_conformance": 3,
    "candidate_screening": 4,
    "candidate_replay": 5,
    "replay_confidence": 6,
    "evaluation": 7,
    "held_out_verification": 8,
    "apply": 9,
    "post_apply": 10,
    # Typed causal events use lifecycle stage names rather than gate names.
    "capability_compile": 3,
    "capability_preflight": 3,
    "task_rollout": 5,
    "evaluator": 7,
    "post_apply_verification": 10,
}


class SelfImprovementCampaignStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    BUDGET_LIMITED = "budget_limited"
    EXHAUSTED = "exhausted"
    COMPLETE = "complete"


class SelfImprovementDispositionKind(str, Enum):
    COMPLETE = "complete"
    CONTINUE_CANDIDATE = "continue_candidate"
    RETRY_INFRASTRUCTURE = "retry_infrastructure"
    HANDOFF_GOAL = "handoff_goal"
    PAUSE_OPERATOR = "pause_operator"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True)
class CampaignUsage:
    tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    wall_seconds: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if isinstance(self.tokens, bool) or self.tokens < 0:
            raise ValueError("campaign token usage must be non-negative")
        object.__setattr__(self, "cost_usd", Decimal(str(self.cost_usd)))
        object.__setattr__(self, "wall_seconds", Decimal(str(self.wall_seconds)))
        if self.cost_usd < 0 or self.wall_seconds < 0:
            raise ValueError("campaign decimal usage must be non-negative")

    def __add__(self, other: "CampaignUsage") -> "CampaignUsage":
        if not isinstance(other, CampaignUsage):
            return NotImplemented
        return CampaignUsage(
            tokens=self.tokens + other.tokens,
            cost_usd=self.cost_usd + other.cost_usd,
            wall_seconds=self.wall_seconds + other.wall_seconds,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "tokens": self.tokens,
            "cost_usd": str(self.cost_usd),
            "wall_seconds": str(self.wall_seconds),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CampaignUsage":
        return cls(
            tokens=_non_negative_int(value.get("tokens"), "campaign tokens"),
            cost_usd=Decimal(str(value.get("cost_usd", "0"))),
            wall_seconds=Decimal(str(value.get("wall_seconds", "0"))),
        )


@dataclass(frozen=True)
class SelfImprovementProgress:
    deepest_stage_rank: int = 0
    semantic_frontier_ids: tuple[str, ...] = ()
    constraint_ids: tuple[str, ...] = ()
    passed_gate_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.deepest_stage_rank, bool) or self.deepest_stage_rank < 0:
            raise ValueError("deepest stage rank must be non-negative")
        for field_name in (
            "semantic_frontier_ids",
            "constraint_ids",
            "passed_gate_ids",
        ):
            values = tuple(sorted({str(item) for item in getattr(self, field_name) if str(item)}))
            object.__setattr__(self, field_name, values)

    def delta_from(self, previous: "SelfImprovementProgress | None") -> tuple[str, ...]:
        if previous is None:
            return tuple(sorted((*self.semantic_frontier_ids, *self.constraint_ids)))
        delta = set(self.semantic_frontier_ids) - set(previous.semantic_frontier_ids)
        delta.update(set(self.constraint_ids) - set(previous.constraint_ids))
        if self.deepest_stage_rank > previous.deepest_stage_rank:
            delta.add(f"stage-rank:{self.deepest_stage_rank}")
        return tuple(sorted(delta))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "deepest_stage_rank": self.deepest_stage_rank,
            "semantic_frontier_ids": list(self.semantic_frontier_ids),
            "constraint_ids": list(self.constraint_ids),
            "passed_gate_ids": list(self.passed_gate_ids),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SelfImprovementProgress":
        if value.get("schema_version") != PROGRESS_SCHEMA_VERSION:
            raise ValueError("unsupported self-improvement progress schema")
        return cls(
            deepest_stage_rank=_non_negative_int(
                value.get("deepest_stage_rank"), "deepest stage rank"
            ),
            semantic_frontier_ids=_string_tuple(value.get("semantic_frontier_ids")),
            constraint_ids=_string_tuple(value.get("constraint_ids")),
            passed_gate_ids=_string_tuple(value.get("passed_gate_ids")),
        )


@dataclass(frozen=True)
class SelfImprovementDisposition:
    kind: SelfImprovementDispositionKind
    reason_code: str
    owner: str | None = None
    stage: str | None = None
    scope: str | None = None
    repairable: bool = False
    progress_delta_ids: tuple[str, ...] = ()
    diagnostic_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", SelfImprovementDispositionKind(self.kind))
        if not re.fullmatch(r"[a-z0-9][a-z0-9_]{0,95}", self.reason_code):
            raise ValueError("disposition reason_code must be stable lower-snake-case")
        object.__setattr__(self, "progress_delta_ids", tuple(sorted(set(self.progress_delta_ids))))
        object.__setattr__(self, "diagnostic_refs", tuple(sorted(set(self.diagnostic_refs)))[:16])
        if self.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE and (
            self.owner != "candidate" or not self.repairable
        ):
            raise ValueError("candidate continuation requires candidate-owned repairable work")
        if self.kind is SelfImprovementDispositionKind.RETRY_INFRASTRUCTURE and (
            self.owner != "infrastructure" or not self.repairable
        ):
            raise ValueError(
                "infrastructure retry requires retryable infrastructure ownership"
            )

    @property
    def continuable(self) -> bool:
        return self.kind in {
            SelfImprovementDispositionKind.CONTINUE_CANDIDATE,
            SelfImprovementDispositionKind.RETRY_INFRASTRUCTURE,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": DISPOSITION_SCHEMA_VERSION,
            "kind": self.kind.value,
            "reason_code": self.reason_code,
            "continuable": self.continuable,
            "owner": self.owner,
            "stage": self.stage,
            "scope": self.scope,
            "repairable": self.repairable,
            "progress_delta_ids": list(self.progress_delta_ids),
            "diagnostic_refs": list(self.diagnostic_refs),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SelfImprovementDisposition":
        if value.get("schema_version") != DISPOSITION_SCHEMA_VERSION:
            raise ValueError("unsupported self-improvement disposition schema")
        return cls(
            kind=SelfImprovementDispositionKind(str(value.get("kind"))),
            reason_code=str(value.get("reason_code") or ""),
            owner=_optional_string(value.get("owner")),
            stage=_optional_string(value.get("stage")),
            scope=_optional_string(value.get("scope")),
            repairable=value.get("repairable") is True,
            progress_delta_ids=_string_tuple(value.get("progress_delta_ids")),
            diagnostic_refs=_string_tuple(value.get("diagnostic_refs")),
        )


@dataclass(frozen=True)
class SelfImprovementCampaign:
    campaign_id: str
    objective: str
    status: SelfImprovementCampaignStatus
    request: Mapping[str, Any]
    request_fingerprint: str
    source_fingerprint: str
    source_snapshot: Mapping[str, Any]
    target_fingerprint: str
    verification_fingerprint: str
    max_cycles: int = DEFAULT_MAX_IMPROVEMENT_CYCLES
    cycle_index: int = 0
    run_ids: tuple[str, ...] = ()
    cumulative_usage: CampaignUsage = CampaignUsage()
    latest_progress: SelfImprovementProgress | None = None
    latest_disposition: SelfImprovementDisposition | None = None
    latest_report_path: str | None = None
    goal_handoff_path: str | None = None

    def __post_init__(self) -> None:
        _validate_id(self.campaign_id, "campaign_id")
        object.__setattr__(self, "status", SelfImprovementCampaignStatus(self.status))
        if not str(self.objective).strip():
            raise ValueError("campaign objective must be non-empty")
        if isinstance(self.max_cycles, bool) or self.max_cycles <= 0:
            raise ValueError("max_cycles must be positive")
        if isinstance(self.cycle_index, bool) or not 0 <= self.cycle_index <= self.max_cycles:
            raise ValueError("campaign cycle index is outside its bound")
        if len(self.run_ids) != self.cycle_index:
            raise ValueError("campaign run lineage must match its cycle index")
        for run_id in self.run_ids:
            _validate_id(run_id, "run_id")
        for fingerprint in (
            self.request_fingerprint,
            self.source_fingerprint,
            self.target_fingerprint,
            self.verification_fingerprint,
        ):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint):
                raise ValueError("campaign fingerprints must be full sha256 values")
        request = dict(self.request)
        object.__setattr__(self, "request", request)
        source_snapshot = dict(self.source_snapshot)
        object.__setattr__(self, "source_snapshot", source_snapshot)
        if _fingerprint(request) != self.request_fingerprint:
            raise ValueError("campaign request fingerprint does not match request")
        if _fingerprint(source_snapshot) != self.source_fingerprint:
            raise ValueError("campaign source fingerprint does not match snapshot")
        if _fingerprint(
            {
                "target": request.get("target"),
                "infer_target": request.get("infer_target"),
                "inferred_new_skill_policy": request.get(
                    "inferred_new_skill_policy"
                ),
            }
        ) != self.target_fingerprint:
            raise ValueError("campaign target fingerprint does not match request")
        if _fingerprint(_verification_request(request)) != self.verification_fingerprint:
            raise ValueError("campaign verification fingerprint does not match request")
        if self.status is SelfImprovementCampaignStatus.COMPLETE and (
            self.latest_disposition is None
            or self.latest_disposition.kind is not SelfImprovementDispositionKind.COMPLETE
        ):
            raise ValueError("complete campaign requires a complete disposition")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CAMPAIGN_SCHEMA_VERSION,
            "campaign_id": self.campaign_id,
            "objective": self.objective,
            "status": self.status.value,
            "request": _json_value(self.request),
            "request_fingerprint": self.request_fingerprint,
            "source_fingerprint": self.source_fingerprint,
            "source_snapshot": _json_value(self.source_snapshot),
            "target_fingerprint": self.target_fingerprint,
            "verification_fingerprint": self.verification_fingerprint,
            "max_cycles": self.max_cycles,
            "cycle_index": self.cycle_index,
            "run_ids": list(self.run_ids),
            "cumulative_usage": self.cumulative_usage.to_dict(),
            "latest_progress": (
                self.latest_progress.to_dict() if self.latest_progress is not None else None
            ),
            "latest_disposition": (
                self.latest_disposition.to_dict()
                if self.latest_disposition is not None
                else None
            ),
            "latest_report_path": self.latest_report_path,
            "goal_handoff_path": self.goal_handoff_path,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SelfImprovementCampaign":
        if value.get("schema_version") != CAMPAIGN_SCHEMA_VERSION:
            raise ValueError("unsupported self-improvement campaign schema")
        raw_request = value.get("request")
        raw_usage = value.get("cumulative_usage")
        raw_source_snapshot = value.get("source_snapshot")
        if (
            not isinstance(raw_request, Mapping)
            or not isinstance(raw_source_snapshot, Mapping)
            or not isinstance(raw_usage, Mapping)
        ):
            raise ValueError("campaign request, source snapshot, and usage must be mappings")
        raw_progress = value.get("latest_progress")
        raw_disposition = value.get("latest_disposition")
        return cls(
            campaign_id=str(value.get("campaign_id") or ""),
            objective=str(value.get("objective") or ""),
            status=SelfImprovementCampaignStatus(str(value.get("status"))),
            request=dict(raw_request),
            request_fingerprint=str(value.get("request_fingerprint") or ""),
            source_fingerprint=str(value.get("source_fingerprint") or ""),
            source_snapshot=dict(raw_source_snapshot),
            target_fingerprint=str(value.get("target_fingerprint") or ""),
            verification_fingerprint=str(value.get("verification_fingerprint") or ""),
            max_cycles=_positive_int(value.get("max_cycles"), "max_cycles"),
            cycle_index=_non_negative_int(value.get("cycle_index"), "cycle_index"),
            run_ids=_string_tuple(value.get("run_ids")),
            cumulative_usage=CampaignUsage.from_dict(raw_usage),
            latest_progress=(
                SelfImprovementProgress.from_dict(raw_progress)
                if isinstance(raw_progress, Mapping)
                else None
            ),
            latest_disposition=(
                SelfImprovementDisposition.from_dict(raw_disposition)
                if isinstance(raw_disposition, Mapping)
                else None
            ),
            latest_report_path=_optional_string(value.get("latest_report_path")),
            goal_handoff_path=_optional_string(value.get("goal_handoff_path")),
        )


RunOnce = Callable[..., Mapping[str, Any]]


class SelfImprovementCampaignController:
    def __init__(
        self,
        *,
        workspace_root: str | Path,
        run_once: RunOnce | None = None,
    ) -> None:
        from aworld.self_evolve.store import FilesystemSelfEvolveStore

        self.workspace_root = Path(workspace_root)
        self.store = FilesystemSelfEvolveStore(self.workspace_root)
        self.run_once = run_once or _default_run_once

    def create(
        self,
        request: Mapping[str, Any],
        *,
        max_cycles: int = DEFAULT_MAX_IMPROVEMENT_CYCLES,
    ) -> SelfImprovementCampaign:
        max_cycles = _positive_int(max_cycles, "max_improvement_cycles")
        persistent = persistent_campaign_request(request)
        explicit_total_tokens = request.get("total_run_token_budget")
        explicit_legacy_tokens = request.get("max_run_tokens")
        persistent["_campaign_total_run_token_budget"] = (
            int(explicit_total_tokens)
            if explicit_total_tokens is not None
            else int(explicit_legacy_tokens)
            if explicit_legacy_tokens is not None
            else int(persistent.get("max_run_tokens", 500_000)) * max_cycles
        )
        if str(persistent.get("apply_policy") or "proposal") != "auto_verified":
            raise ValueError("self-improvement campaigns require apply_policy='auto_verified'")
        if not _request_has_source(persistent):
            raise ValueError("a self-improvement campaign requires an eval source")
        request_fingerprint = _fingerprint(persistent)
        source_snapshot = _source_snapshot(
            persistent,
            workspace_root=self.workspace_root,
        )
        seed = hashlib.sha256(
            f"{request_fingerprint}:{time.time_ns()}:{uuid.uuid4().hex}".encode("utf-8")
        ).hexdigest()[:20]
        campaign_id = f"campaign-{seed}"
        objective = str(
            persistent.get("task")
            or f"Reach a verified self-evolve outcome for {persistent.get('target') or 'the inferred target'}"
        )
        campaign = SelfImprovementCampaign(
            campaign_id=campaign_id,
            objective=objective,
            status=SelfImprovementCampaignStatus.ACTIVE,
            request=persistent,
            request_fingerprint=request_fingerprint,
            source_fingerprint=_fingerprint(source_snapshot),
            source_snapshot=source_snapshot,
            target_fingerprint=_fingerprint(
                {
                    "target": persistent.get("target"),
                    "infer_target": persistent.get("infer_target"),
                    "inferred_new_skill_policy": persistent.get(
                        "inferred_new_skill_policy"
                    ),
                }
            ),
            verification_fingerprint=_fingerprint(_verification_request(persistent)),
            max_cycles=max_cycles,
        )
        self.store.write_campaign(campaign)
        return campaign

    def load(self, campaign_id: str) -> SelfImprovementCampaign:
        return self.store.read_campaign(campaign_id)

    def advance_once(
        self,
        campaign: SelfImprovementCampaign,
        *,
        runtime_request: Mapping[str, Any] | None = None,
    ) -> tuple[SelfImprovementCampaign, Mapping[str, Any]]:
        if campaign.status in {
            SelfImprovementCampaignStatus.COMPLETE,
            SelfImprovementCampaignStatus.BUDGET_LIMITED,
            SelfImprovementCampaignStatus.EXHAUSTED,
        }:
            raise ValueError(f"campaign {campaign.campaign_id} is terminal")
        stored = self.store.read_campaign(campaign.campaign_id)
        if stored.to_dict() != campaign.to_dict():
            raise ValueError("campaign checkpoint changed before advance")
        if campaign.cycle_index >= campaign.max_cycles:
            limited = _limit_campaign(campaign, reason_code="campaign_cycle_budget_exhausted")
            self.store.write_campaign(limited)
            return limited, _campaign_summary(limited, {})

        request = dict(campaign.request)
        for key, value in dict(runtime_request or {}).items():
            if value is None:
                continue
            if key in _RUNTIME_ONLY_REQUEST_KEYS:
                request[key] = value
                continue
            if key in campaign.request and _json_value(value) == campaign.request[key]:
                request[key] = value
        try:
            request.update(_remaining_budget_request(campaign))
        except ValueError:
            limited = _limit_campaign(
                campaign,
                reason_code="campaign_cumulative_budget_exhausted",
            )
            self.store.write_campaign(limited)
            return limited, _campaign_summary(limited, {})
        request.pop("_campaign_total_run_token_budget", None)
        next_cycle = campaign.cycle_index + 1
        run_id = f"{campaign.campaign_id}-cycle-{next_cycle:03d}"
        request.update(
            {
                "workspace_root": str(self.workspace_root),
                "campaign_id": campaign.campaign_id,
                "campaign_cycle": next_cycle,
                "campaign_prior_run_ids": campaign.run_ids,
            }
        )
        if campaign.run_ids:
            prior_target = self.store.read_report(campaign.run_ids[-1]).get("target")
            if isinstance(prior_target, Mapping):
                request["campaign_expected_target"] = {
                    "target_type": prior_target.get("target_type"),
                    "target_id": prior_target.get("target_id"),
                }
        expected_report_path = self.store.run_path(run_id) / "report.json"
        if expected_report_path.is_file() and not expected_report_path.is_symlink():
            recovered_report = self.store.read_report(run_id)
            summary = {
                "run_id": run_id,
                "status": recovered_report.get("status"),
                "report_path": str(expected_report_path),
                "selected_candidate_id": recovered_report.get(
                    "selected_candidate_id"
                ),
            }
        else:
            run_path = self.store.run_path(run_id)
            if run_path.exists():
                raise ValueError("campaign generation has an incomplete run checkpoint")
            summary = dict(self.run_once(**request))
        actual_run_id = str(summary.get("run_id") or run_id)
        if actual_run_id != run_id:
            raise ValueError("self-evolve run did not honor campaign run identity")
        report_path = Path(str(summary.get("report_path") or ""))
        if not report_path.is_file():
            raise ValueError("campaign run did not produce a report")
        report = self.store.read_report(actual_run_id)
        progress = self_improvement_progress(report)
        disposition = derive_self_improvement_disposition(
            report,
            previous_progress=campaign.latest_progress,
        )
        try:
            usage = campaign.cumulative_usage + campaign_usage_from_report(report)
        except ValueError:
            usage = campaign.cumulative_usage
            if disposition.kind is not SelfImprovementDispositionKind.COMPLETE:
                disposition = SelfImprovementDisposition(
                    kind=SelfImprovementDispositionKind.EXHAUSTED,
                    reason_code="campaign_usage_telemetry_missing",
                    owner=disposition.owner,
                    stage=disposition.stage,
                    scope=disposition.scope,
                    repairable=False,
                    progress_delta_ids=disposition.progress_delta_ids,
                    diagnostic_refs=disposition.diagnostic_refs,
                )
        status = _status_for_disposition(disposition)
        advanced = replace(
            campaign,
            status=status,
            cycle_index=next_cycle,
            run_ids=(*campaign.run_ids, actual_run_id),
            cumulative_usage=usage,
            latest_progress=progress,
            latest_disposition=disposition,
            latest_report_path=str(report_path),
            goal_handoff_path=None,
        )
        if disposition.continuable and next_cycle >= campaign.max_cycles:
            advanced = _limit_campaign(
                advanced,
                reason_code="campaign_cycle_budget_exhausted",
            )
            disposition = advanced.latest_disposition
            assert disposition is not None
        report["campaign"] = {
            "campaign_id": advanced.campaign_id,
            "cycle": advanced.cycle_index,
            "max_cycles": advanced.max_cycles,
        }
        report["self_improvement_disposition"] = disposition.to_dict()
        self.store.write_report(actual_run_id, report)
        if disposition.kind is SelfImprovementDispositionKind.HANDOFF_GOAL:
            handoff = build_goal_handoff(advanced, report)
            handoff_path = self.store.write_campaign_goal_handoff(
                advanced.campaign_id,
                handoff,
            )
            advanced = replace(advanced, goal_handoff_path=str(handoff_path))
        self.store.write_campaign(advanced)
        summary.update(_campaign_summary(advanced, summary))
        summary["self_improvement_disposition"] = disposition.to_dict()
        return advanced, summary

    def run_bounded(
        self,
        campaign: SelfImprovementCampaign,
        *,
        runtime_request: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        latest: Mapping[str, Any] = {}
        current = campaign
        while current.status in {
            SelfImprovementCampaignStatus.ACTIVE,
            SelfImprovementCampaignStatus.PAUSED,
        }:
            if (
                current.status is SelfImprovementCampaignStatus.PAUSED
                and current.latest_disposition is not None
                and current.latest_disposition.kind
                not in {
                    SelfImprovementDispositionKind.CONTINUE_CANDIDATE,
                    SelfImprovementDispositionKind.RETRY_INFRASTRUCTURE,
                }
            ):
                break
            current, latest = self.advance_once(
                current,
                runtime_request=runtime_request,
            )
            if current.latest_disposition is None or not current.latest_disposition.continuable:
                break
        return _campaign_summary(current, latest)


def run_self_improvement_campaign(
    *,
    workspace_root: str | Path,
    request: Mapping[str, Any],
    max_improvement_cycles: int = DEFAULT_MAX_IMPROVEMENT_CYCLES,
    resume_campaign: str | None = None,
    advance_once_only: bool = False,
    run_once: RunOnce | None = None,
) -> Mapping[str, Any]:
    controller = SelfImprovementCampaignController(
        workspace_root=workspace_root,
        run_once=run_once,
    )
    if resume_campaign:
        runtime_request = {
            key: value
            for key, value in request.items()
            if key in _RUNTIME_ONLY_REQUEST_KEYS and value is not None
        }
        conflicting = sorted(
            key for key in _RESUME_CONFLICT_KEYS if request.get(key) is not None
        )
        if request.get("rerun_evaluator") is True:
            conflicting.append("rerun_evaluator")
        if conflicting:
            raise ValueError(
                "--resume-campaign cannot replace the persisted source/target contract: "
                + ", ".join(conflicting)
            )
        campaign = controller.load(resume_campaign)
        if campaign.status in {
            SelfImprovementCampaignStatus.COMPLETE,
            SelfImprovementCampaignStatus.BUDGET_LIMITED,
            SelfImprovementCampaignStatus.EXHAUSTED,
        }:
            raise ValueError(f"campaign {resume_campaign} is terminal")
        if campaign.status is SelfImprovementCampaignStatus.PAUSED:
            campaign = replace(
                campaign,
                status=SelfImprovementCampaignStatus.ACTIVE,
            )
            controller.store.write_campaign(campaign)
    else:
        runtime_request = dict(request)
        campaign = controller.create(
            request,
            max_cycles=max_improvement_cycles,
        )
    if advance_once_only:
        _, summary = controller.advance_once(
            campaign,
            runtime_request=runtime_request,
        )
        return summary
    return controller.run_bounded(campaign, runtime_request=runtime_request)


def persistent_campaign_request(request: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        str(key): _json_value(value)
        for key, value in request.items()
        if key not in _RUNTIME_ONLY_REQUEST_KEYS
        and key
        not in {
            "campaign_id",
            "campaign_cycle",
            "campaign_prior_run_ids",
            "campaign_expected_target",
            "workspace_root",
        }
    }
    payload.setdefault("apply_policy", "proposal")
    payload.setdefault("max_run_tokens", 500_000)
    return json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def validate_campaign_source_snapshot(
    campaign: SelfImprovementCampaign,
    *,
    workspace_root: str | Path,
) -> None:
    current = _source_snapshot(campaign.request, workspace_root=workspace_root)
    if current != campaign.source_snapshot:
        raise ValueError(
            f"campaign {campaign.campaign_id} source changed since it was created"
        )


def self_improvement_progress(report: Mapping[str, Any]) -> SelfImprovementProgress:
    events = _typed_failure_events(report)
    semantic_ids = tuple(
        sorted(
            {
                *(_event_identity(item) for item in events),
                *_recovery_frontier_identities(report),
            }
        )
    )
    constraint_ids = tuple(sorted(_constraint_identities(report)))
    passed_gates: set[str] = set()
    deepest = 0
    raw_gates = report.get("gate_results")
    if isinstance(raw_gates, list):
        for item in raw_gates:
            if not isinstance(item, Mapping) or item.get("passed") is not True:
                continue
            gate = str(item.get("gate_name") or "")
            if gate:
                passed_gates.add(gate)
                deepest = max(deepest, _STAGE_RANK.get(gate, 0))
    for event in events:
        deepest = max(deepest, _STAGE_RANK.get(str(event.get("stage") or ""), 0))
    return SelfImprovementProgress(
        deepest_stage_rank=deepest,
        semantic_frontier_ids=semantic_ids,
        constraint_ids=constraint_ids,
        passed_gate_ids=tuple(passed_gates),
    )


def derive_self_improvement_disposition(
    report: Mapping[str, Any],
    previous_progress: SelfImprovementProgress | None = None,
) -> SelfImprovementDisposition:
    status = str(report.get("status") or "")
    progress = self_improvement_progress(report)
    delta = progress.delta_from(previous_progress)
    if status == "succeeded":
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.COMPLETE,
            reason_code="verified_run_succeeded",
            progress_delta_ids=delta,
        )

    events = _typed_failure_events(report)
    candidate = next(
        (
            item
            for item in events
            if item.get("owner") == "candidate" and item.get("repairable") is True
        ),
        None,
    )
    if candidate is not None:
        if delta:
            return _event_disposition(
                SelfImprovementDispositionKind.CONTINUE_CANDIDATE,
                "candidate_repair_frontier_progressed",
                candidate,
                delta,
            )
        return _event_disposition(
            SelfImprovementDispositionKind.EXHAUSTED,
            "candidate_repair_frontier_stalled",
            candidate,
            delta,
        )

    terminal = report.get("terminal_cause")
    if (
        isinstance(terminal, Mapping)
        and terminal.get("failure_class") == "infrastructure"
        and terminal.get("retryable") is True
    ):
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.RETRY_INFRASTRUCTURE,
            reason_code="typed_infrastructure_failure",
            owner="infrastructure",
            stage=_optional_string(terminal.get("stage")),
            scope="shared_run",
            repairable=True,
            progress_delta_ids=delta,
        )
    infrastructure = next(
        (
            item
            for item in events
            if item.get("owner") == "infrastructure"
            and item.get("repairable") is True
        ),
        None,
    )
    if infrastructure is not None:
        return _event_disposition(
            SelfImprovementDispositionKind.RETRY_INFRASTRUCTURE,
            "typed_infrastructure_failure",
            infrastructure,
            delta,
        )
    framework = next(
        (
            item
            for item in events
            if item.get("owner") == "framework"
            or (
                item.get("scope") == "shared_run"
                and item.get("owner") != "infrastructure"
            )
        ),
        None,
    )
    if framework is not None:
        return _event_disposition(
            SelfImprovementDispositionKind.HANDOFF_GOAL,
            "typed_framework_or_shared_blocker",
            framework,
            delta,
        )
    non_retryable_infrastructure = next(
        (item for item in events if item.get("owner") == "infrastructure"),
        None,
    )
    if (
        non_retryable_infrastructure is not None
        or (
            isinstance(terminal, Mapping)
            and terminal.get("failure_class") == "infrastructure"
        )
    ):
        if non_retryable_infrastructure is not None:
            return _event_disposition(
                SelfImprovementDispositionKind.PAUSE_OPERATOR,
                "typed_infrastructure_failure_not_retryable",
                non_retryable_infrastructure,
                delta,
            )
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.PAUSE_OPERATOR,
            reason_code="typed_infrastructure_failure_not_retryable",
            owner="infrastructure",
            stage=_optional_string(terminal.get("stage")),
            scope="shared_run",
            repairable=False,
            progress_delta_ids=delta,
        )
    task = next((item for item in events if item.get("owner") == "task"), None)
    if task is not None:
        return _event_disposition(
            SelfImprovementDispositionKind.PAUSE_OPERATOR,
            "typed_task_failure_not_repairable",
            task,
            delta,
        )
    non_repairable_candidate = next(
        (item for item in events if item.get("owner") == "candidate"),
        None,
    )
    if non_repairable_candidate is not None:
        return _event_disposition(
            SelfImprovementDispositionKind.EXHAUSTED,
            "candidate_failure_not_repairable",
            non_repairable_candidate,
            delta,
        )

    attribution = report.get("rejection_attribution")
    if isinstance(attribution, Mapping) and attribution.get("duplicate_only") is True:
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="semantic_duplicate_frontier_exhausted",
            owner="candidate",
            repairable=False,
            progress_delta_ids=delta,
        )
    return SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.PAUSE_OPERATOR,
        reason_code="legacy_report_missing_typed_disposition",
        progress_delta_ids=delta,
    )


def campaign_usage_from_report(report: Mapping[str, Any]) -> CampaignUsage:
    budget = report.get("budget")
    if not isinstance(budget, Mapping):
        raise ValueError("campaign continuation requires a typed budget report")
    ledger = budget.get("ledger")
    spent = (
        ledger.get("spent_by_stage")
        if isinstance(ledger, Mapping)
        else budget.get("spent_by_stage")
    )
    if not isinstance(spent, Mapping):
        raise ValueError("campaign budget report lacks spent_by_stage")
    usage = CampaignUsage()
    for value in spent.values():
        if not isinstance(value, Mapping):
            raise ValueError("campaign stage usage must be a mapping")
        usage = usage + CampaignUsage.from_dict(value)
    return usage


def build_goal_handoff(
    campaign: SelfImprovementCampaign,
    report: Mapping[str, Any],
) -> dict[str, object]:
    disposition = campaign.latest_disposition
    if disposition is None or disposition.kind is not SelfImprovementDispositionKind.HANDOFF_GOAL:
        raise ValueError("goal handoff requires a framework/shared disposition")
    return {
        "schema_version": "aworld.self_evolve.goal_handoff.v1",
        "campaign_id": campaign.campaign_id,
        "campaign_status": campaign.status.value,
        "objective": (
            "Resolve the typed self-evolve framework/shared blocker for campaign "
            f"{campaign.campaign_id}, verify the framework change, then resume the campaign."
        ),
        "latest_run_id": campaign.run_ids[-1],
        "latest_report_path": campaign.latest_report_path,
        "disposition": disposition.to_dict(),
        "semantic_frontier_ids": list(
            campaign.latest_progress.semantic_frontier_ids
            if campaign.latest_progress is not None
            else ()
        )[:32],
        "constraint_ids": list(
            campaign.latest_progress.constraint_ids
            if campaign.latest_progress is not None
            else ()
        )[:32],
        "next_action": f"aworld-cli optimize --resume-campaign {campaign.campaign_id}",
    }


def _default_run_once(**request: Any) -> Mapping[str, Any]:
    import aworld.self_evolve as self_evolve

    return self_evolve.optimize_from_cli_request(**request)


def _limit_campaign(
    campaign: SelfImprovementCampaign,
    *,
    reason_code: str,
) -> SelfImprovementCampaign:
    disposition = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.EXHAUSTED,
        reason_code=reason_code,
        owner=(campaign.latest_disposition.owner if campaign.latest_disposition else None),
        stage=(campaign.latest_disposition.stage if campaign.latest_disposition else None),
        scope=(campaign.latest_disposition.scope if campaign.latest_disposition else None),
    )
    return replace(
        campaign,
        status=SelfImprovementCampaignStatus.BUDGET_LIMITED,
        latest_disposition=disposition,
    )


def _status_for_disposition(
    disposition: SelfImprovementDisposition,
) -> SelfImprovementCampaignStatus:
    if disposition.kind is SelfImprovementDispositionKind.COMPLETE:
        return SelfImprovementCampaignStatus.COMPLETE
    if disposition.continuable:
        return SelfImprovementCampaignStatus.ACTIVE
    if disposition.kind is SelfImprovementDispositionKind.EXHAUSTED:
        if disposition.reason_code in {
            "campaign_cumulative_budget_exhausted",
            "campaign_cycle_budget_exhausted",
            "campaign_usage_telemetry_missing",
        }:
            return SelfImprovementCampaignStatus.BUDGET_LIMITED
        return SelfImprovementCampaignStatus.EXHAUSTED
    return SelfImprovementCampaignStatus.PAUSED


def _campaign_summary(
    campaign: SelfImprovementCampaign,
    latest: Mapping[str, Any],
) -> dict[str, Any]:
    summary = dict(latest)
    summary.update(
        {
            "campaign_id": campaign.campaign_id,
            "campaign_status": campaign.status.value,
            "campaign_cycle": campaign.cycle_index,
            "campaign_max_cycles": campaign.max_cycles,
            "campaign_path": str(
                Path(".aworld")
                / "self_evolve"
                / "campaigns"
                / campaign.campaign_id
                / "campaign.json"
            ),
            "goal_handoff_path": campaign.goal_handoff_path,
        }
    )
    if campaign.latest_disposition is not None:
        summary["self_improvement_disposition"] = campaign.latest_disposition.to_dict()
    return summary


def _remaining_budget_request(campaign: SelfImprovementCampaign) -> dict[str, Any]:
    request = campaign.request
    token_ceiling = request.get("_campaign_total_run_token_budget")
    if token_ceiling is None:
        token_ceiling = request.get("total_run_token_budget")
    if token_ceiling is None:
        token_ceiling = request.get("max_run_tokens", 500_000)
    remaining_tokens = _remaining_int(token_ceiling, campaign.cumulative_usage.tokens)
    remaining_cost = _remaining_decimal(
        request.get("max_run_cost_usd"), campaign.cumulative_usage.cost_usd
    )
    remaining_wall = _remaining_decimal(
        request.get("max_run_wall_seconds"), campaign.cumulative_usage.wall_seconds
    )
    if remaining_tokens is not None and remaining_tokens <= 0:
        raise ValueError("campaign token budget is exhausted")
    if remaining_cost is not None and remaining_cost <= 0:
        raise ValueError("campaign cost budget is exhausted")
    if remaining_wall is not None and remaining_wall <= 0:
        raise ValueError("campaign wall-time budget is exhausted")
    per_cycle_tokens = _positive_int(
        request.get("max_run_tokens", 500_000),
        "max_run_tokens",
    )
    payload: dict[str, Any] = {
        "total_run_token_budget": (
            min(remaining_tokens, per_cycle_tokens)
            if remaining_tokens is not None
            else per_cycle_tokens
        )
    }
    if remaining_cost is not None:
        payload["max_run_cost_usd"] = remaining_cost
    if remaining_wall is not None:
        payload["max_run_wall_seconds"] = remaining_wall
    return payload


def _typed_failure_events(report: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    events: dict[str, dict[str, Any]] = {}
    for item in _walk_mappings(report):
        owner = item.get("owner")
        stage = item.get("stage")
        scope = item.get("scope")
        repairable = item.get("repairable")
        if (
            owner not in {"candidate", "task", "infrastructure", "framework"}
            or not isinstance(stage, str)
            or scope not in {"variant", "member", "candidate", "shared_run"}
            or not isinstance(repairable, bool)
        ):
            continue
        normalized = {
            "semantic_key": item.get("semantic_key"),
            "code": str(item.get("code") or "typed_failure"),
            "owner": owner,
            "stage": stage,
            "scope": scope,
            "repairable": repairable,
            "category": str(item.get("category") or ""),
            "capability_identity_digest": item.get("capability_identity_digest"),
            "requirement_identity_digest": item.get("requirement_identity_digest"),
            "contract_identity_digest": item.get("contract_identity_digest"),
            "diagnostic_refs": _public_diagnostic_refs(item),
        }
        events.setdefault(_event_identity(normalized), normalized)
    return tuple(events[key] for key in sorted(events))


def _event_identity(event: Mapping[str, Any]) -> str:
    supplied = event.get("semantic_key")
    if isinstance(supplied, str) and supplied:
        return supplied
    return "campaign-frontier-" + hashlib.sha256(
        json.dumps(
            {
                key: event.get(key)
                for key in (
                    "code",
                    "owner",
                    "stage",
                    "scope",
                    "repairable",
                    "category",
                    "capability_identity_digest",
                    "requirement_identity_digest",
                    "contract_identity_digest",
                )
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _constraint_identities(report: Mapping[str, Any]) -> set[str]:
    identities: set[str] = set()
    for item in _walk_mappings(report):
        for key in (
            "schema_field_constraints",
            "fixture_probe_constraints",
            "repair_constraints",
        ):
            constraints = item.get(key)
            if not isinstance(constraints, list):
                continue
            for constraint in constraints:
                identity = _typed_constraint_identity(constraint, kind=key)
                if identity is not None:
                    identities.add(identity)
    return identities


def _recovery_frontier_identities(report: Mapping[str, Any]) -> set[str]:
    """Return monotonic, payload-free progress identities for recovery gains."""

    identities: set[str] = set()
    seen_traces: set[str] = set()
    for item in _walk_mappings(report):
        if item.get("schema_version") != RECOVERY_TRACE_SCHEMA_VERSION:
            continue
        trace = validate_public_recovery_trace(item)
        if trace is None:
            continue
        fingerprint = _fingerprint(trace)
        if fingerprint in seen_traces:
            continue
        seen_traces.add(fingerprint)
        members = trace.get("members")
        if not isinstance(members, list):
            continue
        for member in members:
            if not isinstance(member, Mapping):
                continue
            identity = member.get("member_identity")
            if not isinstance(identity, str) or not identity:
                continue
            repetitions = member.get("candidate_repetition_count")
            success_rate = member.get("candidate_success_rate")
            success_count = 0
            if (
                isinstance(repetitions, (int, float))
                and not isinstance(repetitions, bool)
                and isinstance(success_rate, (int, float))
                and not isinstance(success_rate, bool)
            ):
                success_count = max(
                    0,
                    min(64, round(float(repetitions) * float(success_rate))),
                )
            digest = identity.removeprefix("sha256:")
            for index in range(success_count):
                identities.add(f"recovery-member-{digest}-success-{index + 1}")
            if member.get("classification") == "stable_recovery":
                identities.add(f"recovery-member-{digest}-stable")
            transition_count = member.get("failure_to_success_transition_count")
            if isinstance(transition_count, (int, float)) and not isinstance(
                transition_count, bool
            ):
                for index in range(max(0, min(64, int(transition_count)))):
                    identities.add(
                        f"recovery-member-{digest}-transition-{index + 1}"
                    )
    return identities


def _typed_constraint_identity(value: Any, *, kind: str) -> str | None:
    if not isinstance(value, Mapping) or value.get("kind") == "bounded_public_summary":
        return None
    supplied = value.get("constraint_identity_digest")
    if isinstance(supplied, str) and supplied:
        return "constraint-" + supplied.removeprefix("sha256:")
    if kind == "schema_field_constraints":
        required = ("schema_layer", "field_path", "rule")
    elif kind == "fixture_probe_constraints":
        required = ("kind", "path")
    else:
        required = ("identity",)
    if not all(isinstance(value.get(field), str) and value.get(field) for field in required):
        return None
    canonical = {
        key: value.get(key)
        for key in sorted(value)
        if key
        not in {
            "actual_fingerprint",
            "actual_type",
            "affected_case_ids",
            "occurrence_count",
        }
    }
    return "constraint-" + _fingerprint(canonical)[7:]


def _walk_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_mappings(child)


def _event_disposition(
    kind: SelfImprovementDispositionKind,
    reason_code: str,
    event: Mapping[str, Any],
    delta: tuple[str, ...],
) -> SelfImprovementDisposition:
    return SelfImprovementDisposition(
        kind=kind,
        reason_code=reason_code,
        owner=str(event.get("owner")),
        stage=str(event.get("stage")),
        scope=str(event.get("scope")),
        repairable=event.get("repairable") is True,
        progress_delta_ids=delta,
        diagnostic_refs=_string_tuple(event.get("diagnostic_refs")),
    )


def _public_diagnostic_refs(event: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("artifact_refs", "diagnostic_refs", "evidence_refs"):
        raw = event.get(key)
        if not isinstance(raw, (list, tuple)):
            continue
        for item in raw:
            text = str(item).strip()
            if text and "\n" not in text and "\r" not in text:
                refs.append(text[:500])
    return sorted(set(refs))[:16]


def _verification_request(request: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "apply_policy",
        "auto_apply_target_types",
        "baseline_replay_repetitions",
        "candidate_replay_repetitions",
        "inferred_new_skill_policy",
        "iterations",
        "judge_config",
        "judge_repetitions",
        "judge_timeout_seconds",
        "min_eval_cases",
        "min_score_delta",
        "replay_candidate_limit",
        "replay_enabled",
        "replay_max_steps",
        "replay_stability_margin",
        "replay_timeout_seconds",
    )
    return {key: request.get(key) for key in keys}


def _request_has_source(request: Mapping[str, Any]) -> bool:
    return any(request.get(key) is not None for key in _SOURCE_REQUEST_KEYS)


def _source_snapshot(
    request: Mapping[str, Any],
    *,
    workspace_root: str | Path,
) -> dict[str, Any]:
    root = Path(workspace_root)
    file_keys = {
        "batch_config",
        "dataset",
        "from_trajectory",
        "from_trajectory_set",
    }
    snapshot: dict[str, Any] = {}
    for key in sorted(_SOURCE_REQUEST_KEYS):
        value = request.get(key)
        if value is None:
            snapshot[key] = None
            continue
        if key not in file_keys or not isinstance(value, str):
            snapshot[key] = {"value_fingerprint": _fingerprint(value)}
            continue
        source_path = Path(value).expanduser()
        if not source_path.is_absolute():
            source_path = root / source_path
        entry: dict[str, Any] = {"path": value}
        if source_path.is_file() and not source_path.is_symlink():
            digest = hashlib.sha256()
            size = 0
            with source_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            entry.update(
                {
                    "state": "file",
                    "sha256": digest.hexdigest(),
                    "size": size,
                }
            )
        elif source_path.is_symlink():
            entry["state"] = "symlink"
        else:
            entry["state"] = "missing"
        snapshot[key] = entry
    return snapshot


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_value(model_dump())
    raise TypeError(f"campaign request value is not serializable: {type(value).__name__}")


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _remaining_int(value: Any, spent: int) -> int | None:
    if value is None:
        return None
    return max(0, int(value) - spent)


def _remaining_decimal(value: Any, spent: Decimal) -> Decimal | None:
    if value is None:
        return None
    return max(Decimal("0"), Decimal(str(value)) - spent)


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be positive")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be positive") from exc
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive")
    return parsed


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be non-negative")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be non-negative") from exc
    if parsed < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return parsed


def _validate_id(value: str, field_name: str) -> None:
    if not _ID_RE.fullmatch(str(value)) or value in {".", ".."}:
        raise ValueError(f"invalid {field_name}: {value!r}")


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item))


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
