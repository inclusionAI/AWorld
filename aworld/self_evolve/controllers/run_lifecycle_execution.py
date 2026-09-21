"""Small typed coordinator for one explicit self-evolve run."""

from __future__ import annotations

from dataclasses import dataclass

from aworld.self_evolve.controllers.run_configuration import RunnerConstructionResult
from aworld.self_evolve.controllers.run_execution import ExplicitTargetRunRequest
from aworld.self_evolve.controllers.run_lifecycle_bootstrap_execution import (
    execute_lifecycle_bootstrap,
)
from aworld.self_evolve.controllers.run_lifecycle_iteration_execution import (
    execute_lifecycle_iteration,
    prepare_lifecycle_iteration,
)
from aworld.self_evolve.controllers.run_lifecycle_terminal_execution import (
    execute_lifecycle_terminal,
)
from aworld.self_evolve.controllers.run_phase_assembly import (
    RunPhaseExecutions,
    assemble_run_phases,
)
from aworld.self_evolve.controllers.run_phase_context import (
    RunCompatibilityOverrides,
    RunLifecyclePublishedState,
    RunLifecycleServices,
    RunPhaseContext,
)
from aworld.self_evolve.controllers.run_resources import (
    RunFailureCleanup as _RunFailureCleanup,
)
from aworld.self_evolve.types import CandidateVariant, SelfEvolveRun


@dataclass(frozen=True)
class RunLifecycleResult:
    run: SelfEvolveRun
    selected_candidate: CandidateVariant | None


class RunLifecycleExecution:
    """Coordinates typed bootstrap, iteration, and terminal steps."""

    def __init__(
        self,
        construction: RunnerConstructionResult,
        *,
        services: RunLifecycleServices,
        published_state: RunLifecyclePublishedState,
        compatibility_overrides: RunCompatibilityOverrides,
        execution_telemetry: object,
        active_target_intent: object | None,
        screening_observation_dataset_fingerprint: str | None,
    ) -> None:
        self.phases: RunPhaseExecutions = assemble_run_phases(
            construction,
            services=services,
            published_state=published_state,
            overrides=compatibility_overrides,
            execution_telemetry=execution_telemetry,
            active_target_intent=active_target_intent,
            screening_observation_dataset_fingerprint=(
                screening_observation_dataset_fingerprint
            ),
        )

    @property
    def context(self) -> RunPhaseContext:
        return self.phases.context

    async def execute(
        self,
        *,
        request: ExplicitTargetRunRequest,
        failure_cleanup: _RunFailureCleanup,
    ) -> RunLifecycleResult:
        bootstrap = execute_lifecycle_bootstrap(
            request=request,
            failure_cleanup=failure_cleanup,
            context=self.context,
        )
        if bootstrap.early_run is not None:
            return RunLifecycleResult(bootstrap.early_run, None)
        preparation = prepare_lifecycle_iteration(
            request=request,
            bootstrap=bootstrap,
            context=self.context,
        )
        terminal_request = await execute_lifecycle_iteration(
            preparation,
            context=self.context,
            phases=self.phases,
        )
        terminal = await execute_lifecycle_terminal(
            terminal_request,
            context=self.context,
            phases=self.phases,
        )
        return RunLifecycleResult(terminal.completed_run, terminal.selected_candidate)
