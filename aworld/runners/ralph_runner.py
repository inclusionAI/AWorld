# coding: utf-8
# Copyright (c) inclusionAI.
from aworld.core.task import Runner, Task, TaskResponse
from aworld.logs.util import logger
from aworld.runners.event_runner import TaskEventRunner
from aworld.runners.ralph.evaluator import IterationEvaluationResult, IterationEvaluator
from aworld.runners.ralph.detect.types import StopType
from aworld.runners.ralph.input_builder import IterationInput, IterationInputBuilder
from aworld.runners.ralph.loop_control import (
    AcceptanceEvidence,
    ExecutionLimits,
    LoopDecision,
    LoopDisposition,
    decide_after_attempt,
)
from aworld.runners.ralph.policy import RalphLoopPolicy
from aworld.utils.run_util import exec_tasks
from aworld.runners.ralph.config import RalphConfig
from aworld.runners.ralph.detect.detector import create_stop_detector
from aworld.runners.ralph.state import LoopState, to_loop_context
from aworld.runners.ralph.types import CompletionCriteria


class RalphRunner(Runner):
    """Pipeline mode of RalphRunner implementing Ralph pattern.

    The RALPH pattern consists of:
    - R (Run): Execute tasks with strategic planning
    - A (Analyze): Validate outputs against multiple criteria
    - L (Learn): Reflect on execution and extract insights
    - P (Plan): Replan based on feedback and learnings
    - H (Halt): Detect termination conditions
    """

    def __init__(self, task: Task, completion_criteria: CompletionCriteria = None, **kwargs):
        super().__init__(**kwargs)
        self.task = Task(input=task) if isinstance(task, str) else task
        self.ralph_config = self.task.conf if self.task.conf else RalphConfig.create(
            model_config=task.swarm.ordered_agents[0].conf.llm_config if task.swarm else task.agent.conf.llm_config)
        self.completion_criteria = completion_criteria or CompletionCriteria()
        self.original_task_input = self.task.input

        # State management
        self.loop_context = None
        self.task_context = None
        self.policy = RalphLoopPolicy.from_config(self.ralph_config)
        self.memory_store = None
        self.input_builder = None
        self.evaluator = None
        self._last_execution_result = None
        self._last_executed_iteration = 0
        self._last_verified_iteration = 0

        # Initialize components
        self._init_stop_detector()

    async def pre_run(self):
        self.loop_context = to_loop_context(await TaskEventRunner.build_context(self.task),
                                            completion_criteria=self.completion_criteria,
                                            loop_state=LoopState(confirmation_threshold=1),
                                            work_dir=self.ralph_config.workspace)
        self.memory_store = self.loop_context.memory
        self.input_builder = IterationInputBuilder(policy=self.policy, memory_store=self.memory_store)
        self.evaluator = IterationEvaluator(
            context=self.loop_context,
            memory_store=self.memory_store,
            verify_config=self.ralph_config.verify,
        )

    async def do_run(self):
        execution_result = TaskResponse()
        while True:
            cur_task = self.task
            iter_num = self.loop_context.iteration + 1

            # 1. Check stop conditions
            logger.info(f"Iteration {iter_num} Stop condition check...")
            stop_decision = await self.stop_detector.should_stop(self.loop_context)
            if stop_decision.should_stop:
                should_terminate = await self._maybe_verify_before_completion(stop_decision)
                if should_terminate:
                    logger.info(f"Loop terminated: {stop_decision.stop_type}, Reason: {stop_decision.reason}")
                    break

            # 2. Execute task
            self.loop_context.iteration = iter_num
            logger.info(f"Iteration {iter_num} Executing task...")
            try:
                execution_result = await self._execute_task(cur_task, iter_num=iter_num)
                self._last_execution_result = execution_result
                self._last_executed_iteration = iter_num
                evaluation = await self._evaluate_iteration(
                    task=cur_task,
                    iter_num=iter_num,
                    execution_result=execution_result,
                    phase="post_iteration",
                )
                decision = await self._decide_after_attempt(
                    task=cur_task,
                    iter_num=iter_num,
                    execution_result=execution_result,
                    evaluation=evaluation,
                )
                if decision.disposition is LoopDisposition.COMPLETE:
                    break
                if decision.disposition is LoopDisposition.LIMIT_REACHED:
                    self._mark_limit_reached(execution_result)
                    break
            except Exception as exc:
                logger.exception(f"Error executing Ralph attempt for task: {cur_task.id}")
                execution_result = TaskResponse(
                    id=cur_task.id,
                    success=False,
                    status="incomplete",
                    semantic_status="incomplete",
                    completion_reason="attempt_error",
                    recoverable=True,
                    failure_origin="infrastructure",
                    failure_code=type(exc).__name__,
                    msg=str(exc),
                )
                self._last_execution_result = execution_result
                self._last_executed_iteration = iter_num
                decision = self._record_attempt_decision(
                    iter_num=iter_num,
                    evidence=AcceptanceEvidence(semantic_succeeded=False),
                )
                if decision.disposition is LoopDisposition.LIMIT_REACHED:
                    self._mark_limit_reached(execution_result)
                    break

        return execution_result

    def _semantic_succeeded(self, execution_result: TaskResponse) -> bool:
        status = getattr(execution_result, "semantic_status", None)
        if status is not None:
            return status == "succeeded"
        return bool(getattr(execution_result, "success", False))

    def _verification_required(self) -> bool:
        verify = self.ralph_config.verify
        return bool(
            verify.enabled
            and verify.commands
            and (verify.run_on_each_iteration or verify.run_before_completion)
        )

    async def _decide_after_attempt(
        self,
        *,
        task: Task,
        iter_num: int,
        execution_result: TaskResponse,
        evaluation: IterationEvaluationResult,
    ) -> LoopDecision:
        semantic_succeeded = self._semantic_succeeded(execution_result)
        verify_result = evaluation.verify_result
        if (
            semantic_succeeded
            and self._verification_required()
            and verify_result is None
            and not self.ralph_config.verify.run_on_each_iteration
            and self.ralph_config.verify.run_before_completion
        ):
            evaluation = await self._evaluate_iteration(
                task=task,
                iter_num=iter_num,
                execution_result=execution_result,
                phase="before_completion",
            )
            verify_result = evaluation.verify_result

        evidence = AcceptanceEvidence(
            semantic_succeeded=semantic_succeeded,
            completion_claimed=semantic_succeeded,
            verification_required=self._verification_required(),
            verification_passed=(
                verify_result.passed if verify_result is not None else None
            ),
        )
        return self._record_attempt_decision(iter_num=iter_num, evidence=evidence)

    def _record_attempt_decision(
        self,
        *,
        iter_num: int,
        evidence: AcceptanceEvidence,
    ) -> LoopDecision:
        decision = decide_after_attempt(
            evidence,
            current_iteration=iter_num,
            limits=ExecutionLimits(
                max_iterations=self.completion_criteria.max_iterations
            ),
        )
        state = self.loop_context.loop_state
        if decision.acceptance_satisfied:
            state.completion_confirmations += 1
            state.successful_steps += 1
            state.consecutive_failures = 0
        else:
            state.completion_confirmations = 0
            state.failed_steps += 1
            state.consecutive_failures += 1
        state.metadata["last_attempt_receipt"] = {
            "schema_version": "aworld.ralph.attempt-receipt/v1",
            "iteration": iter_num,
            "semantic_succeeded": evidence.semantic_succeeded,
            "completion_claimed": evidence.completion_claimed,
            "verification_required": evidence.verification_required,
            "verification_passed": evidence.verification_passed,
            "acceptance_satisfied": decision.acceptance_satisfied,
            "disposition": decision.disposition.value,
            "decision_reason": decision.reason,
        }
        return decision

    def _mark_limit_reached(self, execution_result: TaskResponse) -> None:
        execution_result.success = False
        execution_result.status = "budget_exhausted"
        execution_result.semantic_status = "budget_exhausted"
        execution_result.completion_reason = "max_iterations"
        execution_result.recoverable = False

    def _init_stop_detector(self):
        detectors = self.ralph_config.stop_condition.stop_detectors or []
        self.stop_detector = create_stop_detector(custom_detectors=detectors)

    def _should_verify_before_completion(self, stop_decision) -> bool:
        return bool(
            self.evaluator is not None
            and self.ralph_config.verify.enabled
            and self.ralph_config.verify.run_before_completion
            and self._last_execution_result is not None
            and self._last_executed_iteration > 0
            and self._last_verified_iteration != self._last_executed_iteration
            and stop_decision.stop_type in {StopType.COMPLETION, StopType.CUSTOM_STOPPED}
        )

    async def _maybe_verify_before_completion(self, stop_decision) -> bool:
        if not self._should_verify_before_completion(stop_decision):
            return True

        evaluation = await self._evaluate_iteration(
            task=self.task,
            iter_num=self._last_executed_iteration,
            execution_result=self._last_execution_result,
            phase="before_completion",
        )
        if evaluation.verify_result is not None and not evaluation.verify_result.passed:
            self.loop_context.loop_state.completion_confirmations = 0
            logger.info("Pre-completion verification failed; continuing Ralph loop for another repair iteration.")
            return False
        return True

    async def _evaluate_iteration(
        self,
        task: Task,
        iter_num: int,
        execution_result: TaskResponse,
        phase: str,
    ):
        evaluation = await self.evaluator.evaluate(
            task=task,
            iter_num=iter_num,
            execution_result=execution_result,
            phase=phase,
        )
        if evaluation.verify_result is not None:
            self._last_verified_iteration = iter_num
        return evaluation

    async def _execute_task(self, task: Task, iter_num: int) -> TaskResponse:
        iteration_input = await self.input_builder.build(
            task_id=task.id,
            original_task=self.original_task_input,
            iteration=iter_num,
        )
        task.input = iteration_input.task_input
        self.task_context = await self._build_iteration_context(iteration_input, task, iter_num)
        task.context = self.task_context

        results = await exec_tasks(tasks=[task])
        execution_result: TaskResponse = results.get(task.id)
        await self.loop_context.add_file(filename=f"{task.id}_{iter_num}", content=execution_result.answer)
        await self.loop_context.write_to_loop_context(content='',
                                                      task_context=self.task_context,
                                                      iter_num=iter_num,
                                                      reuse_context=self.ralph_config.reuse_context)
        return execution_result

    async def _build_iteration_context(self, iteration_input: IterationInput, task: Task, iter_num: int):
        if self.policy.execution_mode == "reuse_context":
            return to_loop_context(self.loop_context, work_dir=self.ralph_config.workspace)

        return to_loop_context(
            await self.loop_context.build_sub_context(
                sub_task_content=iteration_input.task_input,
                sub_task_id=task.id,
                task=task,
            ),
            work_dir=self.ralph_config.workspace,
        )
