from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import aworld.self_evolve.campaign as campaign_module

from aworld.self_evolve.campaign import (
    CampaignMeasurementLedgerV2,
    CampaignMeasurementOutcomeV2,
    CampaignMeasurementProjection,
    CandidateImprovementOutcome,
    MeasurementExecutionStatus,
    SelfImprovementCampaign,
    SelfImprovementCampaignController,
    run_self_improvement_campaign,
)


def _outcome(
    execution: MeasurementExecutionStatus,
    improvement: CandidateImprovementOutcome = CandidateImprovementOutcome.UNKNOWN,
    *,
    release_gates_passed: bool = False,
    continuation_available: bool = False,
    reason_code: str = "fixture_outcome",
) -> CampaignMeasurementOutcomeV2:
    return CampaignMeasurementOutcomeV2(
        execution_status=execution,
        improvement_outcome=improvement,
        release_gates_passed=release_gates_passed,
        continuation_available=continuation_available,
        reason_code=reason_code,
    )


def _mock_valid_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    *,
    candidate_id: str,
) -> None:
    monkeypatch.setattr(
        campaign_module,
        "_measurement_resume_checkpoint",
        lambda store, *, run_id, report: (
            SimpleNamespace(candidate_id=candidate_id)
            if (
                store.run_path(run_id) / "candidates" / f"{candidate_id}.json"
            ).is_file()
            else None
        ),
    )


@pytest.mark.parametrize(
    ("outcome", "projection"),
    (
        (
            _outcome(
                MeasurementExecutionStatus.COMPLETED,
                CandidateImprovementOutcome.POSITIVE,
                release_gates_passed=True,
            ),
            CampaignMeasurementProjection.SUCCEEDED,
        ),
        (
            _outcome(
                MeasurementExecutionStatus.COMPLETED,
                CandidateImprovementOutcome.NO_EFFECT,
            ),
            CampaignMeasurementProjection.CANDIDATE_REJECTED,
        ),
        (
            _outcome(
                MeasurementExecutionStatus.COMPLETED,
                CandidateImprovementOutcome.REGRESSION,
            ),
            CampaignMeasurementProjection.CANDIDATE_REJECTED,
        ),
        (
            _outcome(
                MeasurementExecutionStatus.CHECKPOINTED,
                continuation_available=True,
                reason_code="checkpoint_quantum_expired",
            ),
            CampaignMeasurementProjection.MEASUREMENT_INCOMPLETE,
        ),
        (
            _outcome(MeasurementExecutionStatus.INVALID),
            CampaignMeasurementProjection.MEASUREMENT_INVALID,
        ),
        (
            _outcome(MeasurementExecutionStatus.FRAMEWORK_BLOCKED),
            CampaignMeasurementProjection.FRAMEWORK_BLOCKED,
        ),
        (
            _outcome(
                MeasurementExecutionStatus.CHECKPOINTED,
                reason_code="campaign_wall_deadline_expired",
            ),
            CampaignMeasurementProjection.EXHAUSTED,
        ),
    ),
)
def test_campaign_measurement_projection_is_causal(
    outcome: CampaignMeasurementOutcomeV2,
    projection: CampaignMeasurementProjection,
) -> None:
    assert outcome.projection is projection
    assert CampaignMeasurementOutcomeV2.from_dict(outcome.to_dict()) == outcome


def test_campaign_measurement_outcome_rejects_non_canonical_persistence() -> None:
    outcome = _outcome(
        MeasurementExecutionStatus.CHECKPOINTED,
        continuation_available=True,
    )
    payload = outcome.to_dict()
    payload["projection"] = "candidate_rejected"
    with pytest.raises(ValueError, match="not canonical"):
        CampaignMeasurementOutcomeV2.from_dict(payload)

    payload = outcome.to_dict()
    payload["continuation_available"] = "true"
    with pytest.raises(ValueError, match="strict"):
        CampaignMeasurementOutcomeV2.from_dict(payload)


def test_measurement_ledger_is_idempotent_and_rejects_counter_drift() -> None:
    ledger = CampaignMeasurementLedgerV2().charge_continuation("run-1")
    assert ledger.charge_continuation("run-1") == ledger
    assert CampaignMeasurementLedgerV2.from_dict(ledger.to_dict()) == ledger

    payload = ledger.to_dict()
    payload["continuation_count"] = 2
    with pytest.raises(ValueError, match="not canonical"):
        CampaignMeasurementLedgerV2.from_dict(payload)


def test_success_requires_completed_positive_measurement_and_release_gates() -> None:
    with pytest.raises(ValueError, match="release gates"):
        _outcome(
            MeasurementExecutionStatus.CHECKPOINTED,
            release_gates_passed=True,
            continuation_available=True,
        )
    with pytest.raises(ValueError, match="improvement outcome"):
        _outcome(MeasurementExecutionStatus.COMPLETED)
    assert (
        _outcome(
            MeasurementExecutionStatus.COMPLETED,
            CandidateImprovementOutcome.POSITIVE,
            release_gates_passed=False,
        ).projection
        is CampaignMeasurementProjection.CANDIDATE_REJECTED
    )


def _budget() -> dict[str, object]:
    return {
        "ledger": {
            "spent_by_stage": {
                "candidate_generation": {
                    "tokens": 10,
                    "cost_usd": "0.01",
                    "wall_seconds": "1",
                }
            }
        }
    }


def _write_report(
    root: Path,
    run_id: str,
    outcome: CampaignMeasurementOutcomeV2,
    *,
    candidate_id: str | None = None,
    authoritative_candidate_count: int = 0,
    legacy_status: str = "rejected",
) -> dict[str, object]:
    report: dict[str, object] = {
        "run_id": run_id,
        "status": legacy_status,
        "budget": _budget(),
        "campaign_measurement_outcome": outcome.to_dict(),
        "verification_funnel": {
            "authoritative_candidate_count": authoritative_candidate_count,
        },
    }
    run_root = root / ".aworld" / "self_evolve" / run_id
    if candidate_id is not None:
        report["selected_candidate_id"] = candidate_id
        candidate_path = run_root / "candidates" / f"{candidate_id}.json"
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text("{}", encoding="utf-8")
    report_path = run_root / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return {
        "run_id": run_id,
        "status": report["status"],
        "report_path": str(report_path),
    }


@pytest.mark.parametrize(
    "deadline_reason",
    ("checkpoint_quantum_expired", "campaign_wall_deadline_expired"),
)
def test_deadline_continuation_does_not_charge_candidate_or_retry(
    tmp_path: Path,
    deadline_reason: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    _mock_valid_checkpoint(monkeypatch, candidate_id="candidate-resume")

    def run_once(**request):
        calls.append(request)
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        if len(calls) == 1:
            return _write_report(
                tmp_path,
                run_id,
                _outcome(
                    MeasurementExecutionStatus.CHECKPOINTED,
                    continuation_available=True,
                    reason_code=deadline_reason,
                ),
                candidate_id="candidate-resume",
                authoritative_candidate_count=1,
            )
        return _write_report(
            tmp_path,
            run_id,
            _outcome(
                MeasurementExecutionStatus.COMPLETED,
                CandidateImprovementOutcome.POSITIVE,
                release_gates_passed=True,
                reason_code="verified_measurement_succeeded",
            ),
            authoritative_candidate_count=1,
        )

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_improvement_cycles=1,
        run_once=run_once,
    )

    assert len(calls) == 2
    assert calls[1]["campaign_measurement_pending_candidate_id"] == (
        "candidate-resume"
    )
    assert result["campaign_status"] == "complete"
    assert result["campaign_measurement_projection"] == "succeeded"
    assert result["campaign_measurement_retry_count"] == 0
    assert result["campaign_measurement_continuation_count"] == 1
    assert result["campaign_candidate_cycle_count"] == 1
    assert result["campaign_authoritative_candidate_count"] == 1


@pytest.mark.parametrize(
    ("outcome", "campaign_status", "projection"),
    (
        (
            _outcome(
                MeasurementExecutionStatus.COMPLETED,
                CandidateImprovementOutcome.NO_EFFECT,
                reason_code="minimum_effect_not_met",
            ),
            "paused",
            "candidate_rejected",
        ),
        (
            _outcome(
                MeasurementExecutionStatus.INVALID,
                reason_code="invalid_control",
            ),
            "paused",
            "measurement_invalid",
        ),
        (
            _outcome(
                MeasurementExecutionStatus.FRAMEWORK_BLOCKED,
                reason_code="shared_runtime_failure",
            ),
            "paused",
            "framework_blocked",
        ),
        (
            _outcome(
                MeasurementExecutionStatus.CHECKPOINTED,
                reason_code="campaign_wall_deadline_expired",
            ),
            "exhausted",
            "exhausted",
        ),
    ),
)
def test_controller_projects_typed_terminal_outcome_without_guessing_legacy_status(
    tmp_path: Path,
    outcome: CampaignMeasurementOutcomeV2,
    campaign_status: str,
    projection: str,
) -> None:
    def run_once(**request):
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        return _write_report(tmp_path, run_id, outcome)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_improvement_cycles=1,
        run_once=run_once,
    )

    assert result["campaign_status"] == campaign_status
    assert result["campaign_measurement_projection"] == projection


def test_framework_blocker_handoff_preserves_candidate_without_charging_frontiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_valid_checkpoint(
        monkeypatch,
        candidate_id="candidate-framework-blocked",
    )
    def run_once(**request):
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        return _write_report(
            tmp_path,
            run_id,
            _outcome(
                MeasurementExecutionStatus.FRAMEWORK_BLOCKED,
                continuation_available=True,
                reason_code="evidence_policy_v2_attestation_failed",
            ),
            candidate_id="candidate-framework-blocked",
            # The runner may have admitted the candidate before the shared
            # framework fault. Campaign must not turn that into a conclusion.
            authoritative_candidate_count=1,
        )

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_improvement_cycles=1,
        run_once=run_once,
    )

    assert result["campaign_status"] == "paused"
    assert result["campaign_measurement_projection"] == "framework_blocked"
    assert result["campaign_measurement_retry_count"] == 0
    assert result["campaign_measurement_continuation_count"] == 0
    assert result["campaign_framework_blocked_count"] == 1
    assert result["campaign_candidate_cycle_count"] == 0
    assert result["campaign_authoritative_candidate_count"] == 0
    assert result["campaign_measurement_pending_candidate_id"] == (
        "candidate-framework-blocked"
    )
    handoff_path = Path(str(result["goal_handoff_path"]))
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert handoff["disposition"]["kind"] == "handoff_goal"
    assert handoff["disposition"]["reason_code"] == (
        "evidence_policy_v2_attestation_failed"
    )
    assert handoff["disposition"]["owner"] == "framework"
    assert handoff["disposition"]["scope"] == "shared_run"


def test_invalid_measurement_retry_uses_only_invalid_retry_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    _mock_valid_checkpoint(
        monkeypatch,
        candidate_id="candidate-invalid-measurement",
    )

    def run_once(**request):
        nonlocal calls
        calls += 1
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        if calls == 1:
            return _write_report(
                tmp_path,
                run_id,
                _outcome(
                    MeasurementExecutionStatus.INVALID,
                    continuation_available=True,
                    reason_code="invalid_control",
                ),
                candidate_id="candidate-invalid-measurement",
            )
        return _write_report(
            tmp_path,
            run_id,
            _outcome(
                MeasurementExecutionStatus.COMPLETED,
                CandidateImprovementOutcome.POSITIVE,
                release_gates_passed=True,
                reason_code="verified_measurement_succeeded",
            ),
            authoritative_candidate_count=1,
        )

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_improvement_cycles=1,
        run_once=run_once,
    )

    assert calls == 2
    assert result["campaign_status"] == "complete"
    assert result["campaign_measurement_retry_count"] == 1
    assert result["campaign_measurement_continuation_count"] == 0
    assert result["campaign_candidate_cycle_count"] == 1


def test_typed_outcome_overrides_conflicting_legacy_succeeded_scalar(
    tmp_path: Path,
) -> None:
    def run_once(**request):
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        return _write_report(
            tmp_path,
            run_id,
            _outcome(
                MeasurementExecutionStatus.COMPLETED,
                CandidateImprovementOutcome.POSITIVE,
                release_gates_passed=False,
                reason_code="release_gate_failed",
            ),
            legacy_status="succeeded",
        )

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_improvement_cycles=1,
        run_once=run_once,
    )

    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert result["status"] == "rejected"
    assert result["campaign_status"] == "paused"
    assert result["campaign_measurement_projection"] == "candidate_rejected"
    assert report["status"] == "rejected"


def test_campaign_reader_migrates_legacy_retry_count_to_one_ledger_source(
    tmp_path: Path,
) -> None:
    controller = SelfImprovementCampaignController(workspace_root=tmp_path)
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=1,
    )
    migrated = replace(
        campaign,
        cycle_index=1,
        run_ids=("legacy-run-1",),
        measurement_ledger=CampaignMeasurementLedgerV2(
            invalid_retry_run_ids=("legacy-run-1",)
        ),
    )
    payload = migrated.to_dict()
    payload.pop("measurement_ledger")
    payload.pop("measurement_continuation_count")

    loaded = SelfImprovementCampaign.from_dict(payload)

    assert loaded.measurement_retry_count == 1
    assert loaded.measurement_continuation_count == 0
    assert loaded.to_dict()["measurement_ledger"]["invalid_retry_run_ids"] == [
        "legacy-run-1"
    ]
