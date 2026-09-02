"""Typed challenger proposal generation, admission, accounting, and persistence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from aworld.self_evolve.budget import BudgetDecision, BudgetStage
from aworld.self_evolve.challenger import (
    ChallengeProposalBatch,
    ChallengeReport,
    ChallengerBackend,
    ChallengerRequest,
    admit_challenge_proposals,
)
from aworld.self_evolve.controllers.run_budget_support import (
    backend_proves_zero_budget_usage,
)
from aworld.self_evolve.controllers.run_resources import RunBudgetContext
from aworld.self_evolve.controllers.run_observers import safe_emit_progress
from aworld.self_evolve.failure_events import FailureOwner, FailureScope
from aworld.self_evolve.regression import ResolvedRegressionSuite
from aworld.self_evolve.sanitization import sanitize_text
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SelfEvolveTarget
from aworld.self_evolve.types import CandidateVariant, GateResult


@dataclass(frozen=True)
class ChallengeExecutionRequest:
    run_id: str
    target: SelfEvolveTarget
    candidate: CandidateVariant
    budget_context: RunBudgetContext | None = None


@dataclass(frozen=True)
class ChallengeExecutionPolicy:
    enabled: bool
    max_cases: int
    regression_suites: tuple[ResolvedRegressionSuite, ...]


@dataclass(frozen=True)
class ChallengeExecutionRuntime:
    store: FilesystemSelfEvolveStore
    backend: ChallengerBackend | None
    progress_callback: Callable[[str, str], object] | None = None


@dataclass(frozen=True)
class ChallengeExecutionResult:
    report: ChallengeReport | None
    gate: GateResult

    def as_tuple(self) -> tuple[ChallengeReport | None, GateResult]:
        return self.report, self.gate


class ChallengeExecutionOverride(Protocol):
    async def __call__(
        self,
        request: ChallengeExecutionRequest,
    ) -> ChallengeExecutionResult: ...


@dataclass(frozen=True)
class ChallengeExecution:
    policy: ChallengeExecutionPolicy
    runtime: ChallengeExecutionRuntime
    override: ChallengeExecutionOverride | None = None

    async def execute(
        self,
        request: ChallengeExecutionRequest,
    ) -> ChallengeExecutionResult:
        if self.override is not None:
            return await self.override(request)
        return await execute_challenge(request, self.policy, self.runtime)


def _admission_gate(
    *,
    passed: bool,
    reason: str,
    details: dict[str, object],
) -> ChallengeExecutionResult:
    return ChallengeExecutionResult(
        report=None,
        gate=GateResult(
            gate_name="challenger_admission",
            passed=passed,
            reason=reason,
            details=details,
        ),
    )


async def execute_challenge(
    request: ChallengeExecutionRequest,
    policy: ChallengeExecutionPolicy,
    runtime: ChallengeExecutionRuntime,
) -> ChallengeExecutionResult:
    if not policy.enabled:
        return _admission_gate(
            passed=True,
            reason="challenger plane is disabled by explicit configuration",
            details={
                "enabled": False,
                "approval_authority": False,
                "admitted_count": 0,
            },
        )
    if not policy.regression_suites:
        return _admission_gate(
            passed=False,
            reason="challenger requires independent regression source suites",
            details={
                "enabled": True,
                "approval_authority": False,
                "failure_class": "infrastructure",
                "failure_owner": FailureOwner.FRAMEWORK.value,
                "failure_scope": FailureScope.SHARED_RUN.value,
                "repairable": False,
                "code": "challenger_source_suites_missing",
            },
        )
    if runtime.backend is None:
        return _admission_gate(
            passed=False,
            reason="challenger backend is unavailable",
            details={
                "enabled": True,
                "approval_authority": False,
                "failure_class": "infrastructure",
                "failure_owner": FailureOwner.FRAMEWORK.value,
                "failure_scope": FailureScope.SHARED_RUN.value,
                "repairable": False,
                "code": "challenger_backend_missing",
            },
        )

    challenge_budget: BudgetDecision | None = None
    budget_context = request.budget_context
    if budget_context is not None and not backend_proves_zero_budget_usage(
        runtime.backend,
        BudgetStage.CHALLENGER,
    ):
        challenge_budget = budget_context.reserve(
            BudgetStage.CHALLENGER,
            f"{request.candidate.candidate_id}-challenger",
        )
        if not challenge_budget.allowed:
            diagnostic = {
                "schema_version": "aworld.self_evolve.challenger_failure.v1",
                "candidate_id": request.candidate.candidate_id,
                "status": "failed",
                "approval_authority": False,
                "code": "challenger_budget_denied",
                "budget_decision": challenge_budget.to_dict(),
            }
            runtime.store.write_challenge_report(
                request.run_id,
                request.candidate.candidate_id,
                diagnostic,
            )
            return _admission_gate(
                passed=False,
                reason="challenger proposal generation budget was denied",
                details={
                    "enabled": True,
                    "approval_authority": False,
                    "failure_class": "budget",
                    "failure_owner": FailureOwner.FRAMEWORK.value,
                    "failure_scope": FailureScope.SHARED_RUN.value,
                    "repairable": False,
                    **diagnostic,
                },
            )
    try:
        safe_emit_progress(
            runtime.progress_callback,
            "challenger",
            "Generating independent challenge proposals",
        )
        challenger_request = ChallengerRequest(
            candidate=request.candidate,
            current_content=request.target.load_current_content(),
            regression_suites=policy.regression_suites,
            max_cases=policy.max_cases,
        )
        batch = await runtime.backend.propose(challenger_request)
        if not isinstance(batch, ChallengeProposalBatch):
            raise TypeError("challenger backend must return ChallengeProposalBatch")
        report = admit_challenge_proposals(
            batch,
            candidate=request.candidate,
            current_content=challenger_request.current_content,
            regression_suites=policy.regression_suites,
        )
        safe_emit_progress(
            runtime.progress_callback,
            "challenger",
            "Persisting admitted independent challenge proposals",
        )
        runtime.store.write_challenge_report(
            request.run_id,
            request.candidate.candidate_id,
            report,
        )
        rejected = tuple(
            admission for admission in report.admissions if not admission.admitted
        )
        return ChallengeExecutionResult(
            report=report,
            gate=GateResult(
                gate_name="challenger_admission",
                passed=not rejected,
                reason=(
                    "challenger proposals were admitted as independent tests"
                    if report.admitted_count
                    else "challenger found no applicable independent probe"
                    if not rejected
                    else "challenger proposals failed deterministic admission"
                ),
                details={
                    "enabled": True,
                    "approval_authority": False,
                    "challenger_id": report.batch.challenger_id,
                    "batch_fingerprint": report.batch.fingerprint,
                    "proposal_count": len(report.admissions),
                    "admitted_count": report.admitted_count,
                    "rejected_count": len(rejected),
                    "rejection_codes": [item.reason_code for item in rejected],
                    **(
                        {}
                        if not rejected
                        else {
                            "failure_class": "infrastructure",
                            "failure_owner": FailureOwner.FRAMEWORK.value,
                            "failure_scope": FailureScope.SHARED_RUN.value,
                            "repairable": False,
                            "code": "challenger_admission_failed",
                        }
                    ),
                },
            ),
        )
    except Exception as exc:
        diagnostic = {
            "schema_version": "aworld.self_evolve.challenger_failure.v1",
            "candidate_id": request.candidate.candidate_id,
            "status": "failed",
            "approval_authority": False,
            "code": "challenger_generation_failed",
            "type": type(exc).__name__,
            "reason": sanitize_text(str(exc), max_chars=240),
        }
        runtime.store.write_challenge_report(
            request.run_id,
            request.candidate.candidate_id,
            diagnostic,
        )
        return _admission_gate(
            passed=False,
            reason="challenger proposal generation failed",
            details={
                "enabled": True,
                "approval_authority": False,
                "failure_class": "infrastructure",
                "failure_owner": FailureOwner.FRAMEWORK.value,
                "failure_scope": FailureScope.SHARED_RUN.value,
                "repairable": False,
                **diagnostic,
            },
        )
    finally:
        if challenge_budget is not None and challenge_budget.allowed:
            assert budget_context is not None
            budget_context.debit(
                challenge_budget,
                actual_source="reserved_fallback_challenger_generation",
            )


__all__ = [
    "ChallengeExecution",
    "ChallengeExecutionPolicy",
    "ChallengeExecutionOverride",
    "ChallengeExecutionRequest",
    "ChallengeExecutionResult",
    "ChallengeExecutionRuntime",
    "execute_challenge",
]
