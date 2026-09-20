"""Transactional and verified-only application phase factory."""

from __future__ import annotations

from aworld.self_evolve.controllers.run_phase_context import RunPhaseContext

from typing import Callable, Any

from aworld.self_evolve.apply_runtime_support import (
    default_new_skill_registry_refresher as _default_new_skill_registry_refresher,
    default_new_skill_registry_compensator as _default_new_skill_registry_compensator,
    default_post_apply_evaluator as _default_post_apply_evaluator,
)
from aworld.self_evolve.feedback_diagnostics import (
    _typed_gate_feedback_metrics as _typed_gate_feedback_metrics,
)
from aworld.self_evolve.controllers.run_apply_transaction import (
    ApplyTransactionExecution,
    ApplyTransactionPolicy,
    ApplyTransactionRequest,
    ApplyTransactionRuntime,
)
from aworld.self_evolve.controllers.run_verified_only_apply import (
    VerifiedOnlyApplyExecution,
    VerifiedOnlyApplyPolicy,
    VerifiedOnlyApplyRequest,
    VerifiedOnlyApplyRuntime,
)
from aworld.self_evolve.targets import (
    SelfEvolveTarget,
)
from aworld.self_evolve.types import (
    CandidateVariant,
)


class ApplyPhaseFactory:
    def __init__(self, context: RunPhaseContext) -> None:
        self.context = context

    def _auto_apply_execution(
        self,
        *,
        post_apply_evaluator: Callable[[CandidateVariant], Any] | None = None,
        runtime_skill_activator: Callable[[CandidateVariant], Any] | None = None,
        runtime_registry_refresher: Callable[[CandidateVariant], Any] | None = None,
        runtime_skill_compensator: Callable[[CandidateVariant, object | None], Any]
        | None = None,
        runtime_registry_compensator: Callable[[CandidateVariant, object | None], Any]
        | None = None,
        release_state: str = "verified",
        published: bool = True,
    ) -> ApplyTransactionExecution:
        evaluator = post_apply_evaluator or self.context.construction.runtime.post_apply_evaluator
        if evaluator is None:
            raise ValueError("auto_verified apply policy requires post_apply_evaluator")
        return ApplyTransactionExecution(
            ApplyTransactionPolicy(
                release_state=release_state,
                published=published,
            ),
            ApplyTransactionRuntime(
                store=self.context.construction.runtime.store,
                post_apply_evaluator=evaluator,
                runtime_skill_activator=(
                    runtime_skill_activator
                    if runtime_skill_activator is not None
                    else self.context.construction.runtime.runtime_skill_activator
                ),
                runtime_registry_refresher=(
                    runtime_registry_refresher
                    if runtime_registry_refresher is not None
                    else self.context.construction.runtime.runtime_registry_refresher
                ),
                runtime_skill_compensator=(
                    runtime_skill_compensator
                    if runtime_skill_compensator is not None
                    else self.context.construction.runtime.runtime_skill_compensator
                ),
                runtime_registry_compensator=(
                    runtime_registry_compensator
                    if runtime_registry_compensator is not None
                    else self.context.construction.runtime.runtime_registry_compensator
                ),
                progress_callback=self.context.construction.runtime.progress_callback,
            ),
        )

    async def _apply_auto_verified(
        self,
        run_id: str,
        target: SelfEvolveTarget,
        candidate: CandidateVariant,
        expected_package_fingerprint: str | None = None,
        addressed_lesson_ids: tuple[str, ...] = (),
        *,
        post_apply_evaluator: Callable[[CandidateVariant], Any] | None = None,
        runtime_skill_activator: Callable[[CandidateVariant], Any] | None = None,
        runtime_registry_refresher: Callable[[CandidateVariant], Any] | None = None,
        runtime_skill_compensator: Callable[[CandidateVariant, object | None], Any]
        | None = None,
        runtime_registry_compensator: Callable[[CandidateVariant, object | None], Any]
        | None = None,
        release_state: str = "verified",
        published: bool = True,
    ) -> dict[str, object]:
        result = await self.context.require_operations().auto_apply_execution(
            post_apply_evaluator=post_apply_evaluator,
            runtime_skill_activator=runtime_skill_activator,
            runtime_registry_refresher=runtime_registry_refresher,
            runtime_skill_compensator=runtime_skill_compensator,
            runtime_registry_compensator=runtime_registry_compensator,
            release_state=release_state,
            published=published,
        ).execute(
            ApplyTransactionRequest(
                run_id=run_id,
                target=target,
                candidate=candidate,
                expected_package_fingerprint=expected_package_fingerprint,
                addressed_lesson_ids=addressed_lesson_ids,
            )
        )
        return result.report

    def _verified_only_apply_execution(self) -> VerifiedOnlyApplyExecution:
        store = self.context.construction.runtime.store
        progress_callback = self.context.construction.runtime.progress_callback

        def transaction_factory(
            isolated_target: SelfEvolveTarget,
        ) -> ApplyTransactionExecution:
            return ApplyTransactionExecution(
                ApplyTransactionPolicy(
                    release_state="verified_only",
                    published=False,
                ),
                ApplyTransactionRuntime(
                    store=store,
                    post_apply_evaluator=_default_post_apply_evaluator(isolated_target),
                    runtime_skill_activator=lambda _candidate: {
                        "status": "skipped",
                        "reason": ("verified_only does not mutate runtime skill state"),
                    },
                    runtime_registry_refresher=(
                        _default_new_skill_registry_refresher(isolated_target)
                    ),
                    runtime_skill_compensator=lambda _candidate, _token: {
                        "status": "skipped",
                        "reason": ("verified_only does not mutate runtime skill state"),
                    },
                    runtime_registry_compensator=(
                        _default_new_skill_registry_compensator(isolated_target)
                    ),
                    progress_callback=progress_callback,
                ),
            )

        return VerifiedOnlyApplyExecution(
            VerifiedOnlyApplyPolicy(),
            VerifiedOnlyApplyRuntime(
                store=store,
                transaction_factory=transaction_factory,
            ),
        )

    async def _apply_verified_only(
        self,
        run_id: str,
        target: SelfEvolveTarget,
        candidate: CandidateVariant,
        expected_package_fingerprint: str | None = None,
        addressed_lesson_ids: tuple[str, ...] = (),
    ) -> dict[str, object]:
        result = await self.context.require_operations().verified_only_apply_execution().execute(
            VerifiedOnlyApplyRequest(
                run_id=run_id,
                target=target,
                candidate=candidate,
                expected_package_fingerprint=expected_package_fingerprint,
                addressed_lesson_ids=addressed_lesson_ids,
            )
        )
        return result.report
