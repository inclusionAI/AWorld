# coding: utf-8
# Copyright (c) inclusionAI.
from typing import Dict, Any, Optional, List, Tuple, Set

from aworld.core.task import Runner, Task, TaskResponse
from aworld.evaluations.base import Evaluator, EvalCriteria, EvalDataCase, EvalDataset, EvalTarget, Scorer
from aworld.evaluations.eval_targets.delegate_eval import DelegateEvalTarget
from aworld.evaluations.reflect import Reflection, GeneralReflector
from aworld.evaluations.reflect.types import ReflectionInput, ReflectionResult
from aworld.evaluations.scorers import scorer_factory
from aworld.evaluations.types import MetricNames
from aworld.logs.util import logger
from aworld.output import WorkSpace, ArtifactType, Artifact
from aworld.ralph_loop.state.utils import create_context
from aworld.utils.run_util import exec_tasks

from aworld.ralph_loop.config import RalphConfig
from aworld.ralph_loop.detect.detector import create_stop_detector
from aworld.ralph_loop.detect.types import StopState

from aworld.ralph_loop.state.types import LoopContext, LoopState
from aworld.ralph_loop.types import CompletionCriteria


class LightingRalphRunner(Runner):
    def __init__(self, task: Task, completion_criteria: CompletionCriteria, **kwargs):
        super().__init__(**kwargs)
        if isinstance(task, str):
            task = Task(input=task)
        self.task = task
        self.original_input = task.input
        self.completed_tasks: Set[str] = set()
        self.ralph_config = RalphConfig.create(model_config=task.swarm.ordered_agents[0].conf.llm_config)
        self.completion_criteria = completion_criteria

        # State management
        self.loop_context = LoopContext(self.ralph_config.workspace)
        self.loop_state = LoopState()
        self.workspace = WorkSpace(workspace_id=self.ralph_config.workspace)

        # Initialize components
        self._init_validator()
        self._init_reflector()
        self._init_stop_detector()

    async def pre_run(self):
        self.loop_context.check_directories()
        self.loop_state.confirmation_threshold = 1

    async def do_run(self):
        execution_result = TaskResponse()
        while True:
            cur_task = self.task
            self.loop_state.iteration += 1
            iter_num = self.loop_state.iteration

            # 1. Check stop conditions
            logger.info(f"Iteration {iter_num} Stop condition check...")
            stop_decision = await self._check_stop_condition(iter_num=iter_num)
            if stop_decision.should_stop:
                logger.info(f"Loop terminated: {stop_decision.stop_type}, Reason: {stop_decision.reason}")
                break

            # 2. Schedule and Execute task
            logger.info(f"Iteration {iter_num} Executing task...")
            await self._task_preprocessing(cur_task, iter_num=iter_num)
            execution_result = await self._execute_task(cur_task, iter_num=iter_num)

            # 3. Validate output to verify task finished
            validation_result = {"passed": False}
            if self.validator:
                logger.info(f"Iteration {iter_num} Validating output...")
                eval_target = DelegateEvalTarget(output=execution_result.to_dict())
                validation_result = await self._validate(eval_target=eval_target, iter_num=iter_num)
                if not validation_result.get("passed"):
                    logger.warning(f"Task {cur_task.id} validation failed: {validation_result.get('reason')}")
                    self.loop_state.consecutive_failures += 1
                else:
                    logger.info(f"Task {cur_task.id} validation passed (scores: {validation_result.get('scores')})")
                    self.loop_state.consecutive_failures = 0
                    self.completed_tasks.add(cur_task.id)
                    self.loop_state.completion_confirmations = len(self.completed_tasks)

            # 4. Reflect on execution to find improvements
            if self.reflector and not validation_result.get("passed"):
                logger.info(f"Iteration {iter_num} - Reflecting on execution...")
                await self._reflect(execution_result=execution_result,
                                    validation_result=validation_result,
                                    success=execution_result.success,
                                    iter_num=iter_num)
        return execution_result

    def _init_validator(self):
        """Initialize output validator."""

        validators = self.ralph_config.validation.validators or []
        scorers = []
        for validate in validators:
            if isinstance(validate, Scorer):
                scorers.append(validate)
            elif isinstance(validate, EvalCriteria):
                scorers.extend(scorer_factory.get_scorer_instances_for_criterias(validate))
            elif isinstance(validate, str):
                criteria = EvalCriteria(metric_name=validate)
                scorers.extend(scorer_factory.get_scorer_instances_for_criterias(criteria))

        if not scorers:
            defaults = []
            if self.completion_criteria.answer:
                defaults.append(MetricNames.OUTPUT_CORRECTNESS)
            criterias = []
            for metric_name in defaults:
                criteria = EvalCriteria(metric_name=metric_name)
                criterias.append(criteria)
            scorers.extend(scorer_factory.get_scorer_instances_for_criterias(criterias))

        self.validator = Evaluator(scorers=scorers, parallel_num=1)

    def _init_reflector(self):
        """Initialize reflection engine."""

        reflectors = self.ralph_config.reflection.reflectors or []
        if not reflectors:
            reflectors.append(GeneralReflector(model_config=self.ralph_config.reflection.model_config))
        self.reflector = Reflection(reflectors=reflectors)

    def _init_stop_detector(self):
        """Initialize stop condition detector."""
        detectors = self.ralph_config.stop_condition.stop_detectors or []
        self.stop_detector = create_stop_detector(custom_detectors=detectors)

    async def _check_stop_condition(self, iter_num: int) -> Any:
        """Check if loop should stop."""
        stop_state = StopState(loop_context=self.loop_context,
                               loop_state=self.loop_state,
                               completion_criteria=self.completion_criteria,
                               metadata={})
        return await self.stop_detector.should_stop(stop_state)

    async def _task_preprocessing(self, cur_task: Task, iter_num: int):
        info = self.workspace.get_artifact_data(f"{self.loop_context.reflect_dir()}_{cur_task.id}_{iter_num - 1}")
        content = info.get('content', '') if info else ''
        cur_task.input = f"{content}{cur_task.input}"

        if not cur_task.context:
            cur_task.context = await create_context(cur_task)

    async def _execute_task(self, task: Task, iter_num: int) -> TaskResponse:
        """Execute a task and return result and success status."""
        results = await exec_tasks(tasks=[task])
        execution_result: TaskResponse = results.get(task.id)
        return execution_result

    async def _validate(self, eval_target: EvalTarget, iter_num: int) -> Dict[str, Any]:
        case = EvalDataCase(case_data={
            "ground_truth": self.completion_criteria.answer,
            "user_input": self.original_input,
        })
        result = await self.validator.evaluate(dataset=EvalDataset(eval_cases=[case]), eval_target=eval_target)
        case_result = result.eval_case_results[0]

        logger.info(f"Iteration {iter_num} task {eval_target.output.get('id')} "
                    f"validate result: {case_result.score_rows}")

        passed = all(sr.metric_results[k]["eval_status"].value == 1
                     for k, sr in case_result.score_rows.items())
        scores = {
            m: mr["value"]
            for _, sr in case_result.score_rows.items()
            for m, mr in sr.metric_results.items()
        }
        return {"passed": passed,
                "scores": scores,
                "details": case_result.score_rows,
                "reason": "Validation failed" if not passed else "Validation passed"}

    async def _reflect(self,
                       execution_result: TaskResponse,
                       validation_result: Optional[Dict[str, Any]],
                       success: bool,
                       iter_num: int) -> Optional[List]:
        reflect_input = ReflectionInput(
            task_id=execution_result.id,
            iteration=self.loop_state.iteration,
            success=success,
            error_msg=getattr(execution_result, 'error', None) if not success else None,
            input_data=self.task.input,
            output_data=execution_result,
            reference_data=self.completion_criteria.answer,
            validation_data=validation_result,
        )

        reflections = await self.reflector.reflect(reflect_input)
        await self._apply_reflections(reflections, task_id=reflect_input.task_id, iter_num=iter_num)
        return reflections

    async def _apply_reflections(self, reflections: List[ReflectionResult], task_id: str, iter_num: int) -> None:
        all_suggestions = []
        for reflection in reflections:
            if reflection.suggestions:
                all_suggestions.extend(reflection.suggestions)
        if not all_suggestions:
            return

        content = "\n- ".join(all_suggestions)
        artifact = Artifact(artifact_id=f"{self.loop_context.reflect_dir()}_{task_id}_{iter_num}",
                            artifact_type=ArtifactType.TEXT, content=content,
                            metadata={"context_type": "reflect"})
        await self.workspace.add_artifact(artifact, index=False)
