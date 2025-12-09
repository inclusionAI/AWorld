# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
import uuid
import importlib
from typing import Dict, Any, List, Optional, Callable
from aworld.config.conf import EvaluationConfig
from aworld.core.task import Runner
from aworld.evaluations.base import (
    EvalDataCase, EvalDataset, Scorer, EvalCriteria, EvalTarget, EvalTask, Evaluator, MetricNames
)
from aworld.evaluations.recoder.base import EvalRecorder
from aworld.evaluations.recoder.eval_recorder import EvalResultRecorder, EvalDatasetRecorder, EvalTaskRecorder
from aworld.dataset.dataset import Dataset
from aworld.logs.util import logger
from aworld.evaluations.scorers import scorer_factory


class EvaluateRunner(Runner):

    def __init__(self,
                 config: EvaluationConfig = None,
                 task: EvalTask = None,
                 task_recorder: EvalRecorder = EvalTaskRecorder(),
                 dataset_recorder: EvalRecorder = EvalDatasetRecorder(),
                 result_recorder: EvalRecorder = EvalResultRecorder(),):
        self.config = config
        self.task = task
        self.dataset_recorder = dataset_recorder
        self.result_recorder = result_recorder
        self.task_recorder = task_recorder

    async def do_run(self):
        eval_config = self.config
        if not self.task:
            self.task_recorder.eval_config = eval_config
            try:
                eval_task: EvalTask = await self.task_recorder.record(eval_input=None)
            except Exception as e:
                logger.error(f"eval runner create task failed: {str(e)}")
                raise e
        else:
            eval_task = self.task

        try:
            loaded_dataset: EvalDataset = await self.load_dataset(eval_config)
            loaded_dataset.run_id = eval_task.task_id
            eval_dataset: EvalDataset = await self.dataset_recorder.record(eval_input=loaded_dataset)
            scorers = self.get_scorers(eval_config)
            eval_target = self.get_target_for_eval(eval_config)
            evaluator = Evaluator(
                scorers=scorers,
                repeat_times=eval_config.repeat_times,
                parallel_num=eval_config.parallel_num,
                skip_passed_cases=eval_config.skip_passed_cases,
                skip_passed_on_metrics=eval_config.skip_passed_on_metrics,
            )
            result = await evaluator.evaluate(eval_dataset, eval_target)
            await self.result_recorder.record(eval_input=result)
            return result
        except Exception as e:
            logger.error(f"eval run {eval_task.task_id} failed: {str(e)}")
            raise e

    def get_scorers(self, eval_config: EvaluationConfig) -> list[Scorer]:
        """Get scorer instances for evaluation."""
        converted_criterias = []
        has_time_cost_metric = False

        for criteria in eval_config.eval_criterias:
            if isinstance(criteria, dict):
                converted_criteria = EvalCriteria.from_dict(criteria)
                converted_criterias.append(converted_criteria)
                if converted_criteria.metric_name == MetricNames.PREDICT_TIME_COST_MS:
                    has_time_cost_metric = True
            else:
                converted_criterias.append(criteria)
                if criteria.metric_name == MetricNames.PREDICT_TIME_COST_MS:
                    has_time_cost_metric = True

        # add predict_time_cost_ms metric if not exists
        if not has_time_cost_metric:
            time_cost_criteria = EvalCriteria(
                metric_name=MetricNames.PREDICT_TIME_COST_MS,
                min_value=0.0
            )
            converted_criterias.append(time_cost_criteria)

        scorers = scorer_factory(criterias=converted_criterias)
        for scorer in scorers:
            scorer.eval_config = eval_config
        return scorers

    def get_target_for_eval(self, eval_config: EvaluationConfig) -> EvalTarget:
        """Get eval target instance for evaluation."""
        if eval_config.eval_target:
            return eval_config.eval_target
        if not eval_config.eval_target_full_class_name:
            raise ValueError("eval_target_full_class_name must be specified in EvaluationConfig")
        try:
            if '.' in eval_config.eval_target_full_class_name:
                module_path, class_name = eval_config.eval_target_full_class_name.rsplit('.', 1)
            else:
                raise ValueError(
                    f"Invalid full class name format: {eval_config.eval_target_full_class_name}. It should include module path.")
            module = importlib.import_module(module_path)
            eval_target_class = getattr(module, class_name)
            if not issubclass(eval_target_class, EvalTarget):
                raise ValueError(f"Class {eval_config.eval_target_full_class_name} is not a subclass of EvalTarget")
            eval_target_config = eval_config.eval_target_config or {}
            eval_target_instance = eval_target_class(**eval_target_config)
            eval_target_instance.eval_config = eval_config
            return eval_target_instance
        except (ImportError, AttributeError, TypeError) as e:
            logger.error(f"Failed to create EvalTarget instance: {str(e)}")
            raise ValueError(
                f"Failed to create EvalTarget instance from {eval_config.eval_target_full_class_name}: {str(e)}")

    async def load_dataset(self, eval_config: EvaluationConfig) -> EvalDataset:
        """Load the dataset.

        Args:
            eval_config: the evaluation config.

        Returns:
            EvalDataset
        """
        if self._is_file_path(eval_config.eval_dataset_id_or_file_path):
            dataset = Dataset[Dict[str, Any]](name="my_dataset", data=[])
            preload_transform = None
            if eval_config.eval_dataset_preload_transform:
                preload_transform = self._load_preload_transform(eval_config.eval_dataset_preload_transform)
            dataset.load_from(eval_config.eval_dataset_id_or_file_path, preload_transform=preload_transform)
            eval_cases: List[EvalDataCase] = []
            eval_dataset_id = uuid.uuid4().hex
            for data_row in dataset.to_dataloader(batch_size=1,
                                                  shuffle=eval_config.eval_dataset_load_config.shuffle,
                                                  drop_last=eval_config.eval_dataset_load_config.drop_last,
                                                  seed=eval_config.eval_dataset_load_config.seed,
                                                  sampler=eval_config.eval_dataset_load_config.sampler):
                if data_row:
                    eval_cases.append(EvalDataCase(eval_dataset_id=eval_dataset_id, case_data=data_row[0]))

            return EvalDataset(eval_dataset_id=eval_dataset_id, eval_cases=eval_cases)
        else:
            eval_dataset = await self.dataset_recorder.get_by_key(eval_config.eval_dataset_id_or_file_path)

            if not eval_dataset:
                logger.error(f"eval dataset {eval_config.eval_dataset_id_or_file_path} not exists.")
                raise FileNotFoundError(f"eval dataset {eval_config.eval_dataset_id_or_file_path} not exists.")
            return eval_dataset

    def _is_file_path(self, eval_dataset_id_or_file_path: str) -> bool:
        if not eval_dataset_id_or_file_path:
            raise ValueError(f"eval_dataset_id_or_file_path is empty.")
        has_path_separator = '/' in eval_dataset_id_or_file_path
        _, ext = os.path.splitext(eval_dataset_id_or_file_path)
        has_extension = bool(ext)
        return has_path_separator or has_extension

    def _load_preload_transform(self, preload_transform: Optional[str | Callable]) -> Optional[Callable]:
        if isinstance(preload_transform, str):
            try:
                module_path, function_name = preload_transform.rsplit('.', 1)
                module = importlib.import_module(module_path)
                preload_transform = getattr(module, function_name)
                if not callable(preload_transform):
                    raise ValueError(f"Preload transform {preload_transform} is not a callable function.")
                return preload_transform
            except (ImportError, AttributeError) as e:
                logger.error(f"Failed to load preload transform {preload_transform}: {str(e)}")
                raise ValueError(f"Failed to load preload transform {preload_transform}: {str(e)}")
        elif isinstance(preload_transform, Callable):
            return preload_transform
