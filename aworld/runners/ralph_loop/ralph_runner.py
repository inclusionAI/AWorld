# coding: utf-8
# Copyright (c) inclusionAI.
import asyncio
from dataclasses import asdict
from typing import Dict, Any, Optional, Union, List

from aworld.config import TaskConfig, ConfigDict
from aworld.core.task import Runner, Task, TaskResponse
from aworld.evaluations.base import Evaluator, EvalCriteria, EvalDataCase, EvalDataset, EvalTarget
from aworld.evaluations.scorers import scorer_factory
from aworld.runners.ralph_loop.detect.stop_condition import create_stop_detector
from aworld.runners.ralph_loop.detect.types import StopState
from aworld.runners.ralph_loop.reflect import ReflectionLevel, FailureReflector, SuccessReflector, ReflectionInput
from aworld.runners.ralph_loop.reflect.engine import Reflection
from aworld.runners.ralph_loop.state.types import LoopContext, LoopState
from aworld.runners.ralph_loop.types import CompletionCriteria
from aworld.runners.ralph_loop.validate.target import DelegateEvalTarget
from aworld.runners.ralph_loop.validate.types import ValidationMetrics
from aworld.logs.util import logger
from aworld.utils.run_util import exec_tasks


class RalphRunner(Runner):
    def __init__(self, task: Task, completion_criteria: CompletionCriteria = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.task = task

        # critical task record
        self.critical_tasks: Dict[str, Task] = {}
        # record primitive user goal
        self.original_input = task.input

        self.need_plan = True
        if task.agent or not task.swarm:
            self.need_plan = False
            self.critical_tasks[task.id] = task
        else:
            # todo: search for previously run tasks
            # todo: use default auto process
            pass

        task_config = task.conf
        if not task_config:
            task_config = ConfigDict({"workspace": ".", "stop_detectors": [], "validators": [], "reflectors": []})
        else:
            if isinstance(task_config, TaskConfig):
                pass

        self.task_config = task_config
        # context and state initialization
        self.loop_context = LoopContext()
        self.loop_state = LoopState()

        # stop detection
        if not completion_criteria:
            completion_criteria = CompletionCriteria()
        self.completion_criteria = completion_criteria

        self.stop_detector = task_config.stop_detectors
        if not self.stop_detector:
            self.stop_detector = create_stop_detector()

        # validation
        validators = task_config.validators
        self.validator = self._create_validator(validators)

        # reflection
        self.reflector = task_config.reflectors
        if not self.reflector:
            self.reflector = Reflection(
                reflectors=[
                    FailureReflector(level=ReflectionLevel.DEEP),
                    SuccessReflector(level=ReflectionLevel.MEDIUM),
                ]
            )

    async def pre_run(self):
        # record task
        pass

    async def do_run(self):
        self.loop_context.check_directories()

        cur_task = self.task
        if self.need_plan:
            # todo: load task, context and mission process
            pass

        while True:
            self.loop_state.iteration += 1

            stop_decision = await self._check_stop_condition()
            if stop_decision.should_stop:
                stop_reason = stop_decision.stop_type
                logger.info(f'Loop terminated with reason: {stop_reason}')
                break

            iteration_start = asyncio.get_event_loop().time()
            results = await exec_tasks(tasks=[cur_task])
            execution_result: TaskResponse = results.get(cur_task.id)
            logger.info(f"Task {cur_task} execution result: {execution_result.answer}")

            if self.need_plan:
                replan = self._replan()
                self.loop_context.update_plan(replan)
                cur_task = self.select_tasks(replan)

            if self.validator:
                logger.info("The result verification start...")
                # Complete information required to evaluate scorer
                eval_target = DelegateEvalTarget(output=asdict(execution_result))
                validation_result = await self._validate(
                    execution_result=execution_result, eval_target=eval_target
                )

                if not validation_result.get("passed"):
                    logger.warning(
                        f"Verification failed, task: {cur_task}, {validation_result.get('reason', 'Unknown')}")
                    self.loop_state.consecutive_failures += 1
                else:
                    logger.info(f"Verification success, task: {cur_task}")
                    self.loop_state.consecutive_failures = 0

            if self.reflector:
                if not self.critical_tasks.get(cur_task.id):
                    logger.info(f"Task {cur_task} is not critical, skip reflection")
                    continue

                logger.info("Reflection start...")
                iteration_time = asyncio.get_event_loop().time() - iteration_start
                reflection_result = await self._reflect(
                    execution_result=execution_result,
                    validation_result=validation_result if self.validator else None,
                    iteration_time=iteration_time,
                )

                if reflection_result:
                    await self._apply_reflection(reflection_result)

    def _create_validator(self, validators: List[Union[dict, Evaluator, EvalCriteria]] = None) -> Evaluator:
        scorers = []
        if validators:
            for validator in validators:
                if isinstance(validator, Evaluator):
                    if validator.scorers:
                        scorers.extend(validator.scorers)
                    continue

                if isinstance(validator, dict):
                    eval_criteria = EvalCriteria(**validator)
                else:
                    eval_criteria = validator
                scorer = scorer_factory.get_scorer_instances_for_criterias(eval_criteria)
                if scorer:
                    scorers.extend(scorer)
        else:
            default_criteria = [
                EvalCriteria(metric_name=ValidationMetrics.FORMAT_CORRECTNESS, threshold=1.0),
                EvalCriteria(metric_name=ValidationMetrics.OUTPUT_CORRECTNESS, threshold=0.9),
                EvalCriteria(metric_name=ValidationMetrics.OUTPUT_QUALITY, threshold=0.8)
            ]

            scorers = scorer_factory.get_scorer_instances_for_criterias(default_criteria)
        evaluator = Evaluator(scorers=scorers, parallel_num=3)
        logger.info(f"validator use {len(scorers)} scorers, {[score.name for score in scorers]}.")
        return evaluator

    async def _check_stop_condition(self) -> Any:
        stop_state = StopState(
            loop_context=self.loop_context,
            loop_state=self.loop_state,
            completion_criteria=self.completion_criteria,
            metadata={},
        )

        return await self.stop_detector.should_stop(stop_state)

    async def _replan(self):
        return None

    async def _validate(
            self, execution_result: TaskResponse, eval_target: EvalTarget
    ) -> Dict[str, Any]:
        case = EvalDataCase(
            case_data={
                "format_type": "text",
                "context": self.original_input,
                "requirement": "The answer should be relevant, complete, and well-structured",
            }
        )
        dataset = EvalDataset(eval_cases=[case])
        result = await self.validator.evaluate(dataset=dataset, eval_target=eval_target)
        case_result = result.eval_case_results[0]

        passed = all(
            sr.metric_results[k]["eval_status"].value == 1
            for k, sr in case_result.score_rows.items()
        )

        scores = {
            m: mr["value"]
            for _, sr in case_result.score_rows.items()
            for m, mr in sr.metric_results.items()
        }

        logger.info(f"validate score: {scores}")
        return {
            "passed": passed,
            "scores": scores,
            "details": case_result,
            "reason": "Validation failed" if not passed else "Validation passed",
        }

    async def _reflect(
            self,
            execution_result: TaskResponse,
            validation_result: Optional[Dict[str, Any]],
            iteration_time: float,
    ) -> Optional[Any]:
        context = ReflectionInput(
            iteration=self.loop_state.iteration,
            success=validation_result["passed"] if validation_result else True,
            error=None,
            input_data={"task": "user task"},
            output_data=execution_result,
            execution_time=iteration_time,
            previous_attempts=[],
            metadata={
                "validation_scores": validation_result["scores"]
                if validation_result
                else {}
            },
        )

        reflections = await self.reflector.reflect(context)

        if reflections:
            for reflection in reflections:
                logger.info(f"reflection summary: {reflection.summary}")
                if reflection.recommendations:
                    logger.info(f"suggestions: {', '.join(reflection.recommendations[:2])}")

        return reflections

    async def _apply_reflection(self, reflections):
        # switch strategies, update parameters or prompt, etc
        logger.info("Use reflection...")
