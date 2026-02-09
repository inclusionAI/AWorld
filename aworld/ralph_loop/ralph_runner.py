# coding: utf-8
# Copyright (c) inclusionAI.
import os
import time
import traceback
from collections import OrderedDict

import yaml
from typing import Dict, Any, Optional, Union, List, Tuple, Set

from pydantic import BaseModel

import aworld
from aworld.config import ModelConfig
from aworld.core.task import Runner, Task, TaskResponse
from aworld.evaluations.base import Evaluator, EvalCriteria, EvalDataCase, EvalDataset, EvalTarget, Scorer
from aworld.evaluations.scorers import scorer_factory
from aworld.logs.util import logger
from aworld.output import WorkSpace, ArtifactType, Artifact
from aworld.ralph_loop.state.utils import create_context

from aworld.runners.state_manager import EventRuntimeStateManager
from aworld.utils.run_util import exec_tasks

from aworld.ralph_loop.config import RalphConfig
from aworld.ralph_loop.detect.detector import create_stop_detector
from aworld.ralph_loop.detect.types import StopState
from aworld.ralph_loop.reflect import Reflection, GeneralReflector
from aworld.ralph_loop.reflect.types import ReflectionInput, ReflectionResult
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
    """

    def __init__(self, task: Union[str, Task], completion_criteria: Optional[CompletionCriteria] = None, **kwargs):
        super().__init__(**kwargs)
        if isinstance(task, str):
            task = Task(input=task)
        self.task = task

        self.tasks: Dict[str, Task] = OrderedDict()
        self.completed_tasks: Set[str] = set()
        # critical task record
        self.critical_tasks: Set[str] = set()
        # record primitive user goal
        self.original_input = task.input

        # init config
        task_config = task.conf
        if isinstance(task_config, RalphConfig):
            self.ralph_config = task_config
        else:
            # LLM is necessary
            model_config = ModelConfig(
                llm_provider=os.getenv("LLM_PROVIDER", "openai"),
                llm_model_name=os.getenv("LLM_MODEL_NAME"),
                llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
                llm_base_url=os.getenv("LLM_BASE_URL"),
                llm_api_key=os.getenv("LLM_API_KEY"),
            )
            if not task_config:
                self.ralph_config = RalphConfig.create(model_config)
            else:
                if isinstance(task_config, BaseModel):
                    task_config = task_config.model_dump()
                ralph_config_path = task_config.get("ralph_config_path")
                if not ralph_config_path:
                    self.ralph_config = RalphConfig.create(model_config)
                else:
                    with open(ralph_config_path, "r") as file:
                        yaml_data = yaml.safe_load(file)

                    conf_dict = RalphConfig.create(model_config).model_dump()
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

        self.need_analyze = True
        if task.agent or not task.swarm:
            # Special task don't need analysis and plan
            self.need_plan = False
            self.need_analyze = False
            self.tasks[task.id] = task
            self.critical_tasks.add(task.id)
        else:
            self.need_plan = self.ralph_config.planning.enabled

        # State management
        self.loop_context = LoopContext(self.ralph_config.workspace)
        self.loop_state = LoopState()
        self.workspace = WorkSpace(workspace_id=self.ralph_config.workspace)
        self.state_manager = EventRuntimeStateManager.instance()

        # Current strategic plan
        self.current_plan = None

        # Initialize components
        self._init_validator()
        self._init_reflector()
        self._init_stop_detector()

        self._init_mission_processor()
        self._init_planner()

    async def pre_run(self):
        """Preparation before loop execution."""

        self.loop_context.check_directories()
        # Process mission

        # Create initial plan

        self.loop_state.confirmation_threshold = len(self.tasks)

    async def do_run(self):
        # todo: task schedule
        cur_task = list(self.tasks.values())[0]
        loop_start_time = time.time()

        execution_result = TaskResponse()
        while True:
            self.loop_state.iteration += 1
            iteration_start = time.time()

            iter_num = self.loop_state.iteration
            # 1. Check stop conditions
            logger.info(f"Iteration {iter_num} [1/5] CHECK - Stop condition check...")
            stop_decision = await self._check_stop_condition(iter_num=iter_num)
            if stop_decision.should_stop:
                logger.info(f"Loop terminated: {stop_decision.stop_type}, Reason: {stop_decision.reason}")
                break

            await self._task_preprocessing(cur_task, iter_num=iter_num)

            # 2. Schedule and Execute task
            logger.info(f"Iteration {iter_num} [2/5] EXECUTE - Executing task...")
            execution_result, execution_success = await self._execute_task(cur_task, iter_num=iter_num)

            if not execution_success:
                self.loop_state.consecutive_failures += 1
                logger.warning(f"Task execution failed (failures: {self.loop_state.consecutive_failures})")
            else:
                logger.info(f"Iteration {iter_num} Task {cur_task.id} execution successfully")

            # 3. Validate output
            validation_result = {"passed": False}
            if self.validator and execution_success:
                logger.info(f"Iteration {iter_num} [3/5] ANALYZE - Validating output...")
                eval_target = DelegateEvalTarget(output=execution_result.to_dict())
                validation_result = await self._validate(eval_target=eval_target, iter_num=iter_num)

                if not validation_result.get("passed"):
                    logger.warning(f"Task {cur_task.id} validation failed: {validation_result.get('reason')}")
                    self.loop_state.consecutive_failures += 1
                    execution_success = False
                else:
                    logger.info(f"Task {cur_task.id} validation passed (scores: {validation_result.get('scores')})")
                    self.loop_state.consecutive_failures = 0
                    self.completed_tasks.add(cur_task.id)
                    self.loop_state.completion_confirmations = len(self.completed_tasks)

            # 4. Reflect on execution
            # validation did not pass or execution failed or task complex
            if self.reflector and (not validation_result.get("passed") or not execution_success):
                logger.info(f"Iteration {iter_num} [4/5] LEARN - Reflecting on execution...")
                iteration_time = time.time() - iteration_start

                reflection_results = await self._reflect(
                    execution_result=execution_result,
                    validation_result=validation_result,
                    iteration_time=iteration_time,
                    success=execution_success,
                    iter_num=iter_num
                )

            # Replan if needed
            if self.need_plan and self.current_plan:
                logger.info(f"Iteration {iter_num} [5/5] REPLAN - Checking if replanning needed...")
                # todo
                # Collect feedback for trigger detection
                # Detect replanning triggers
                # Replan if necessary

            # loop metrics
            iteration_elapsed = time.time() - iteration_start
            self.loop_state.total_time = time.time() - loop_start_time

            logger.info(f"Iteration #{iter_num} completed in {iteration_elapsed:.2f}s")
            logger.info(f"Total time: {self.loop_state.total_time:.2f}s")

        logger.info(f"Ralph Loop Runner - Execution Complete\n"
                    f"Total iterations: {self.loop_state.iteration - 1}\n"
                    f"Total time: {self.loop_state.total_time:.2f}s")
        return execution_result

    def _init_mission_processor(self):
        """Initialize mission processor and analyzer."""

        if aworld.debug_mode:
            logger.info(f"Mission analyzer initialized")

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
            # defaults = [ValidationMetrics.TRAJECTORY_QUALITY]
            defaults = []
            if self.completion_criteria.answer:
                defaults.append(ValidationMetrics.OUTPUT_CORRECTNESS)
            criterias = []
            for metric_name in defaults:
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
            reflectors.append(GeneralReflector(
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

    async def _check_stop_condition(self, iter_num: int) -> Any:
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

    async def _task_preprocessing(self, cur_task: Task, iter_num: int):
        info = self.workspace.get_artifact_data(f"{self.loop_context.reflect_dir()}_{cur_task.id}_{iter_num - 1}")
        if info:
            content = f'{info.get("content")}\n'
        else:
            content = ''
        cur_task.input = f"{content}{cur_task.input}"

        context = await create_context(cur_task)
        cur_task.context = context

    async def _execute_task(self, task: Task, iter_num: int) -> Tuple[TaskResponse, bool]:
        """Execute a task and return result and success status."""

        try:
            results = await exec_tasks(tasks=[task])
            execution_result: TaskResponse = results.get(task.id)

            if execution_result and execution_result.answer:
                logger.info(f"Task output: {execution_result.answer}")
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

    async def _validate(self, eval_target: EvalTarget, iter_num: int) -> Dict[str, Any]:
        case = EvalDataCase(
            case_data={
                "format_type": "text",
                "ground_truth": self.completion_criteria.answer,
                "user_input": self.original_input,
            }
        )
        dataset = EvalDataset(eval_cases=[case])
        result = await self.validator.evaluate(dataset=dataset, eval_target=eval_target)
        case_result = result.eval_case_results[0]

        logger.info(f"Iteration {iter_num} task {eval_target.output.get('id')} "
                    f"validate result: {case_result.score_rows}")

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
            "details": case_result.score_rows,
            "reason": "Validation failed" if not passed else "Validation passed",
        }

    async def _reflect(
            self,
            execution_result: TaskResponse,
            validation_result: Optional[Dict[str, Any]],
            iteration_time: float,
            success: bool,
            iter_num: int
    ) -> Optional[List]:
        """Execute reflection on the iteration."""

        reflect_input = ReflectionInput(
            task_id=execution_result.id,
            iteration=self.loop_state.iteration,
            success=success,
            error_msg=getattr(execution_result, 'error', None) if not success else None,
            input_data=self.task.input,
            output_data=execution_result,
            reference_data=self.completion_criteria.answer,
            validation_data=validation_result,
            execution_time=iteration_time,
            previous_attempts=self.loop_context.trajectories,
        )

        try:
            reflections = await self.reflector.reflect(reflect_input)
        except Exception as e:
            logger.error(f"Reflection failed: {e}")
            if aworld.debug_mode:
                logger.debug(f"Reflection failed: {traceback.format_exc()}")
            return None

        await self._apply_reflections(reflections, task_id=reflect_input.task_id, iter_num=iter_num)

        logger.info(f"Iteration {iter_num} reflection completed: {len(reflections)} results")
        for i, reflection in enumerate(reflections):
            logger.info(f"Reflection {i + 1} ({reflection.reflection_type.value}):\n"
                        f"    · Summary: {reflection.summary}\n"
                        f"    · Findings: {', '.join(reflection.key_findings)}\n"
                        f"    · Insights: {', '.join(reflection.insights)}\n"
                        f"    · Suggestions: {', '.join(reflection.suggestions)}")
        return reflections

    async def _apply_reflections(self, reflections: List[ReflectionResult], task_id: str, iter_num: int) -> None:
        all_suggestions = []
        for reflection in reflections:
            if reflection.suggestions:
                all_suggestions.extend(reflection.suggestions)

        task = self.tasks.get(task_id)
        if not task:
            # something wrong, need rerun
            pass

        if not all_suggestions:
            return

        content = "\n- ".join(all_suggestions)
        artifact = Artifact(artifact_id=f"{self.loop_context.reflect_dir()}_{task_id}_{iter_num}",
                            artifact_type=ArtifactType.TEXT, content=content,
                            metadata={
                                "context_type": "reflect",
                            })
        await self.workspace.add_artifact(artifact, index=False)
