# coding: utf-8
# Copyright (c) inclusionAI.
import time
import traceback

import yaml
from dataclasses import asdict
from typing import Dict, Any, Optional, Union, List, Tuple

from pydantic import BaseModel

import aworld
from aworld.core.task import Runner, Task, TaskResponse
from aworld.evaluations.base import Evaluator, EvalCriteria, EvalDataCase, EvalDataset, EvalTarget, Scorer
from aworld.evaluations.scorers import scorer_factory
from aworld.logs.util import logger
from aworld.runners.state_manager import EventRuntimeStateManager
from aworld.utils.run_util import exec_tasks

from aworld.ralph_loop.config import RalphConfig
from aworld.ralph_loop.detect.stop_condition import create_stop_detector
from aworld.ralph_loop.detect.types import StopState
from aworld.ralph_loop.reflect import Reflection, GeneralReflector
from aworld.ralph_loop.state.types import LoopContext, LoopState
from aworld.ralph_loop.types import CompletionCriteria, ConflictStrategy
from aworld.ralph_loop.validate.target import DelegateEvalTarget
from aworld.ralph_loop.validate.types import ValidationMetrics


class RalphRunner(Runner):
    """Ralph Runner implementing Ralph pattern.

    The RALPH pattern consists of:
    - R (Run): Execute tasks with strategic planning
    - A (Analyze): Validate outputs against multiple criteria
    - L (Learn): Reflect on execution and extract insights
    - P (Plan): Replan based on feedback and learnings
    - H (Halt): Detect termination conditions

    TODO: agentic
    """

    def __init__(self, task: Task, completion_criteria: Optional[CompletionCriteria] = None, **kwargs):
        super().__init__(**kwargs)
        self.task = task

        # critical task record
        self.critical_tasks: Dict[str, Task] = {}
        # record primitive user goal
        self.original_input = task.input

        # init config
        task_config = task.conf
        if not task_config:
            self.ralph_config = RalphConfig.create()
        else:
            if isinstance(task_config, BaseModel):
                task_config = task_config.model_dump()
            ralph_config_path = task_config.get("ralph_config_path")
            if not ralph_config_path:
                self.ralph_config = RalphConfig.create()
            else:
                with open(ralph_config_path, "r") as file:
                    yaml_data = yaml.safe_load(file)

                conf_dict = asdict(RalphConfig.create())
                conf_dict.update(yaml_data)
                self.ralph_config = RalphConfig(**conf_dict)

        # init completion criteria
        if not completion_criteria:
            completion_criteria = CompletionCriteria(
                max_iterations=self.ralph_config.stop_condition.max_iterations,
                timeout=self.ralph_config.stop_condition.timeout,
                max_cost=self.ralph_config.stop_condition.max_cost,
            )
        self.completion_criteria = completion_criteria

        self.need_plan = True
        if task.agent or not task.swarm:
            self.need_plan = False
            self.critical_tasks[task.id] = task
        else:
            self.need_plan = self.ralph_config.planning.enabled

        # State management
        self.loop_context = LoopContext()
        self.loop_state = LoopState()
        self.state_manager = EventRuntimeStateManager.instance()

        # Current strategic plan
        # self.current_plan: Optional[StrategicPlan] = None
        self.current_plan = None

        # Initialize components
        self._init_mission_processor()
        self._init_planner()
        self._init_validator()
        self._init_reflector()
        self._init_stop_detector()
        self._init_replanner()

    async def pre_run(self):
        """Preparation before loop execution."""

        self.loop_context.check_directories()

        # Process mission
        # Create initial plan

    async def do_run(self):
        cur_task = self.task
        loop_start_time = time.time()

        while True:
            self.loop_state.iteration += 1
            iteration_start = time.time()

            logger.info(f"Iteration #{self.loop_state.iteration}")

            # 1. Check stop conditions
            logger.info("\n[1/5] RUN - Stop condition check...")
            stop_decision = await self._check_stop_condition()
            if stop_decision.should_stop:
                logger.info(f"Loop terminated: {stop_decision.stop_type}, Reason: {stop_decision.reason}")
                break

            # 2. Schedule and Execute task
            logger.info("\n[2/5] RUN - Executing task...")
            execution_result, execution_success = await self._execute_task(cur_task)

            if not execution_success:
                self.loop_state.consecutive_failures += 1
                logger.warning(f"Task execution failed (failures: {self.loop_state.consecutive_failures})")
            else:
                logger.info(f"Task {cur_task.id} execution completed successfully")

            # 3. Validate output
            validation_result = None
            if self.validator and execution_success:
                logger.info("\n[3/5] ANALYZE - Validating output...")
                eval_target = DelegateEvalTarget(output=asdict(execution_result))
                validation_result = await self._validate(eval_target=eval_target)

                if not validation_result.get("passed"):
                    logger.warning(f"Validation failed: {validation_result.get('reason')}")
                    self.loop_state.consecutive_failures += 1
                    execution_success = False
                else:
                    logger.info(f"Validation passed (scores: {validation_result.get('scores')})")
                    self.loop_state.consecutive_failures = 0

            # 4. Reflect on execution
            if self.reflector:
                logger.info("\n[4/5] LEARN - Reflecting on execution...")
                iteration_time = time.time() - iteration_start

                reflection_results = await self._reflect(
                    execution_result=execution_result,
                    validation_result=validation_result,
                    iteration_time=iteration_time,
                    success=execution_success
                )

            # Replan if needed
            if self.need_plan and self.current_plan:
                logger.info("\n[5/5] REPLAN - Checking if replanning needed...")
                # todo
                # Collect feedback for trigger detection
                # Detect replanning triggers
                # Replan if necessary

            # loop metrics
            iteration_elapsed = time.time() - iteration_start
            self.loop_state.total_time = time.time() - loop_start_time

            logger.info(f"Iteration #{self.loop_state.iteration} completed in {iteration_elapsed:.2f}s")
            logger.info(f"Total time: {self.loop_state.total_time:.2f}s")

        logger.info(f"Ralph Loop Runner - Execution Complete\n"
                    f"Total iterations: {self.loop_state.iteration}\n"
                    f"Total time: {self.loop_state.total_time:.2f}s")

    def _init_mission_processor(self):
        """Initialize mission processor and analyzer."""

        if aworld.debug_mode:
            logger.info(f"Mission processor and analyzer initialized")

    def _init_planner(self):
        """Initialize strategic planner."""

        if aworld.debug_mode:
            logger.info(f"Planner initialized")

    def _init_validator(self):
        """Initialize output validator."""

        validators = self.ralph_config.validation.validators
        strategy = self.ralph_config.validation.conflict_strategy
        scorers = []
        if not validators:
            strategy = ConflictStrategy.MERGE
        else:
            for validate in validators:
                if isinstance(validate, Scorer):
                    scorers.append(validate)
                elif isinstance(validate, EvalCriteria):
                    scorers.extend(scorer_factory.get_scorer_instances_for_criterias(validate))
                elif isinstance(validate, str):
                    criteria = EvalCriteria(metric_name=validate)
                    scorers.extend(scorer_factory.get_scorer_instances_for_criterias(criteria))

        if strategy == ConflictStrategy.OVERWRITE or strategy == ConflictStrategy.UPDATE:
            pass
        else:
            criterias = []
            for metric_name in [ValidationMetrics.OUTPUT_QUALITY, ValidationMetrics.TRAJECTORY_QUALITY]:
                criteria = EvalCriteria(metric_name=metric_name)
                criterias.append(criteria)
            scorers.extend(scorer_factory.get_scorer_instances_for_criterias(criterias))

        parallel_num = 1
        self.validator = Evaluator(scorers=scorers, parallel_num=parallel_num)

        if aworld.debug_mode:
            logger.info(f"Validator initialized with {len(scorers)} scorers: {[score.name for score in scorers]}")

    def _init_reflector(self):
        """Initialize reflection engine."""

        reflectors = self.ralph_config.reflection.reflectors
        strategy = self.ralph_config.reflection.conflict_strategy
        if not reflectors:
            reflectors = []

        if strategy == ConflictStrategy.OVERWRITE or strategy == ConflictStrategy.UPDATE:
            reflectors = reflectors
        elif strategy == ConflictStrategy.MERGE or strategy == ConflictStrategy.APPEND:
            reflectors = reflectors.append(GeneralReflector(
                model_config=self.ralph_config.reflection.model_config,
            ))
        else:
            reflectors.clear()
            reflectors.append(GeneralReflector(
                model_config=self.ralph_config.reflection.model_config,
            ))

        self.reflector = Reflection(reflectors=reflectors)
        if aworld.debug_mode:
            logger.info(f"Reflection initialized with {len(reflectors)} reflectors")

    def _init_stop_detector(self):
        """Initialize stop condition detector."""
        # Use legacy detector if provided
        detectors = self.ralph_config.stop_condition.stop_detectors
        strategy = self.ralph_config.stop_condition.conflict_strategy
        if strategy == ConflictStrategy.OVERWRITE or strategy == ConflictStrategy.UPDATE:
            self.stop_detector = create_stop_detector(enable_completion=False,
                                                      enable_limits=False,
                                                      enable_failure_detection=False,
                                                      enable_interrupt=False,
                                                      enable_error=False,
                                                      custom_detectors=detectors)
        elif strategy == ConflictStrategy.MERGE or strategy == ConflictStrategy.APPEND:
            self.stop_detector = create_stop_detector(custom_detectors=detectors)
        else:
            logger.warning(f"Unknown stop detector strategy: {strategy}, will use default detectors")
            self.stop_detector = create_stop_detector()

        if aworld.debug_mode:
            logger.info("Stop detector initialized")

    def _init_replanner(self):
        """Initialize replanning module."""

        if aworld.debug_mode:
            logger.info("Replanning initialized")

    async def _check_stop_condition(self) -> Any:
        """Check if loop should stop."""
        stop_state = StopState(
            loop_context=self.loop_context,
            loop_state=self.loop_state,
            completion_criteria=self.completion_criteria,
            metadata={
                'current_plan': self.current_plan,
            },
        )

        return await self.stop_detector.should_stop(stop_state)

    async def _execute_task(self, task: Task) -> Tuple[TaskResponse, bool]:
        """Execute a task and return result and success status."""
        try:
            results = await exec_tasks(tasks=[task])
            execution_result: TaskResponse = results.get(task.id)

            if execution_result and execution_result.answer:
                logger.info(f"Task output: {str(execution_result.answer)[:200]}...")
                return execution_result, True
            else:
                logger.warning("Task execution returned no result")
                return execution_result, False

        except Exception as e:
            logger.error(f"Task execution failed with exception: {e}")
            if aworld.debug_mode:
                logger.debug(f"Task execution failed with exception: {traceback.format_exc()}")
            error_response = TaskResponse(
                id=task.id,
                answer=None,
                msg=str(e)
            )
            return error_response, False

    async def _validate(self, eval_target: EvalTarget) -> Dict[str, Any]:
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
            success: bool,
    ) -> Optional[List]:
        """Execute reflection on the iteration."""
        # Build reflection input with enhanced metadata
