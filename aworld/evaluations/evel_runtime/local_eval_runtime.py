

import os
import json
import importlib
from aworld.evaluations.base import (
    EvalDataCase, EvalRunConfig, EvalResult, EvaluateRunner, EvalDataset, EvalRun, Scorer, EvalTarget, Evaluator
)
from aworld.evaluations.evel_runtime.eval_run_manager import EvalRunManager, DefaultEvalRunManager
from aworld.evaluations.evel_runtime.eval_dataset_manager import EvalDatasetManager, DefaultEvalDatasetManager
from aworld.evaluations.evel_runtime.eval_result_manager import EvalResultManager, DefaultEvalResultManager
from aworld.evaluations.scorers.scorer_registry import get_scorer_instances_for_criterias

from aworld.logs.util import logger


class LocalEvaluateRunner(EvaluateRunner):
    """Local evaluate runner."""

    def __init__(self,
                 eval_run_manager: EvalRunManager = DefaultEvalRunManager(),
                 eval_dataset_manager: EvalDatasetManager = DefaultEvalDatasetManager(),
                 eval_result_manager: EvalResultManager = DefaultEvalResultManager(),
                 ):
        self.eval_run_manager = eval_run_manager
        self.eval_dataset_manager = eval_dataset_manager
        self.eval_result_manager = eval_result_manager

    async def eval_run(self, eval_config: EvalRunConfig) -> EvalResult:
        """Run the evaluation.

        Returns:
            EvaluationResult
        """
        try:
            eval_dataset: EvalDataset = None
            eval_run: EvalRun = await self.eval_run_manager.create_eval_run(eval_config)
            if self._is_file_path(eval_config.eval_dataset_id_or_file_path):
                data_cases = await self._load_dataset_from_file(eval_config.eval_dataset_id_or_file_path)
                eval_dataset = await self.eval_dataset_manager.create_eval_dataset(
                    run_id=eval_run.run_id, dataset_name=f"Dataset_{eval_run.run_id}", data_cases=data_cases)
            else:
                eval_dataset = await self.eval_dataset_manager.get_eval_dataset(eval_config.eval_dataset_id_or_file_path)

            if not eval_dataset:
                logger.error(f"eval dataset {eval_config.eval_dataset_id_or_file_path} not exists.")
                raise FileNotFoundError(f"eval dataset {eval_config.eval_dataset_id_or_file_path} not exists.")

            scorers = self._get_scorers(eval_config)
            eval_target = self._get_target_for_eval(eval_config)
            evaluator = Evaluator(
                scorers=scorers,
                repeat_times=eval_config.repeat_times,
                eval_parallelism=eval_config.eval_parallelism
            )
            result = await evaluator.evaluate(eval_dataset, eval_target)
            await self.eval_result_manager.save_eval_result(result)
            return result
        except Exception as e:
            logger.error(f"eval run {eval_run.run_id} failed: {str(e)}")
            raise e

    def _get_target_for_eval(self, eval_config: EvalRunConfig) -> EvalTarget:
        '''
        Get eval target instance for evaluation.
        '''

        if not eval_config.eval_target_full_class_name:
            raise ValueError("eval_target_full_class_name must be specified in EvalRunConfig")
        try:
            if '.' in eval_config.eval_target_full_class_name:
                module_path, class_name = eval_config.eval_target_full_class_name.rsplit('.', 1)
            else:
                raise ValueError(f"Invalid full class name format: {eval_config.eval_target_full_class_name}. It should include module path.")
            module = importlib.import_module(module_path)
            eval_target_class = getattr(module, class_name)
            if not issubclass(eval_target_class, EvalTarget):
                raise ValueError(f"Class {eval_config.eval_target_full_class_name} is not a subclass of EvalTarget")
            eval_target_config = eval_config.eval_target_config or {}
            eval_target_instance = eval_target_class(**eval_target_config)

            return eval_target_instance
        except (ImportError, AttributeError, TypeError) as e:
            logger.error(f"Failed to create EvalTarget instance: {str(e)}")
            raise ValueError(f"Failed to create EvalTarget instance from {eval_config.eval_target_full_class_name}: {str(e)}")

    def _get_scorers(self, eval_config: EvalRunConfig) -> list[Scorer]:
        '''
        Get scorer instances for evaluation.
        '''
        return get_scorer_instances_for_criterias(eval_config.eval_criterias)

    def _is_file_path(self, eval_dataset_id_or_file_path: str) -> bool:
        if not eval_dataset_id_or_file_path:
            raise ValueError(f"eval_dataset_id_or_file_path is empty.")
        _, ext = os.path.splitext(eval_dataset_id_or_file_path)
        return ext.lower() in [".jsonl"]

    async def _load_dataset_from_file(self, eval_dataset_id_or_file_path: str) -> list[EvalDataCase]:
        if os.path.isfile(eval_dataset_id_or_file_path):
            with open(eval_dataset_id_or_file_path, "r", encoding="utf-8") as f:
                content = f.read()

            try:
                eval_cases = []
                for line in content.strip().split("\n"):
                    if line.strip():
                        case_data = json.loads(line)
                        eval_case = EvalDataCase(case_data=case_data)
                        eval_cases.append(eval_case)
                return eval_cases
            except Exception as e:
                logger.error(f"load eval dataset {eval_dataset_id_or_file_path} failed, error: {e}")
                raise e
        else:
            logger.error(f"eval dataset file {eval_dataset_id_or_file_path} not exists.")
            raise FileNotFoundError(f"eval dataset file {eval_dataset_id_or_file_path} not exists.")
