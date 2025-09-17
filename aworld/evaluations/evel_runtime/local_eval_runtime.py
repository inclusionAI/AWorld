

import os
from aworld.evaluations.base import EvalDataCase, EvalRunConfig, EvalResult, EvaluateRunner, EvalDataset
from aworld.evaluations.evel_runtime.eval_run_manager import EvalRunManager, DefaultEvalRunManager
from aworld.logs.util import logger


class LocalEvaluateRunner(EvaluateRunner):

    def __init__(self, eval_run_manager: EvalRunManager = DefaultEvalRunManager()):
        self.eval_run_manager = eval_run_manager

    async def eval_run(self, eval_config: EvalRunConfig) -> EvalResult:
        """Run the evaluation.

        Returns:
            EvaluationResult
        """
        eval_run = self.eval_run_manager.create_eval_run(eval_config)
        if self._is_file_path(eval_config.eval_dataset_id_or_file_path):
            eval_dataset = self._load_dataset_from_file(eval_config.eval_dataset_id_or_file_path)
        else:

    def _is_file_path(self, eval_dataset_id_or_file_path: str) -> bool:
        if not eval_dataset_id_or_file_path:
            raise ValueError(f"eval_dataset_id_or_file_path is empty.")
        _, ext = os.path.splitext(eval_dataset_id_or_file_path)
        return ext.lower() in [".jsonl"]

    def _load_dataset_from_file(self, eval_dataset_id_or_file_path: str) -> list[EvalDataCase]:
        if os.path.isfile(eval_dataset_id_or_file_path):
            with open(eval_dataset_id_or_file_path, "r", encoding="utf-8") as f:
                content = f.read()

            try:
                eval_cases = []
                for line in content.strip().split("\n"):
                    eval_cases.append(EvalDataCase.model_validate_csv(line))
                return eval_cases
            except Exception as e:
                logger.error(f"load eval dataset {eval_dataset_id_or_file_path} failed, error: {e}")
                raise e
        else:
            logger.error(f"eval dataset file {eval_dataset_id_or_file_path} not exists.")
            raise FileNotFoundError(f"eval dataset file {eval_dataset_id_or_file_path} not exists.")
