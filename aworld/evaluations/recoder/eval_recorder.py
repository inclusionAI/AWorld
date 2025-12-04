# coding: utf-8
# Copyright (c) inclusionAI.
import dataclasses

from aworld.evaluations.base import EvalResult, EvalTask, EvalDataset
from aworld.evaluations.recoder.base import EvalRecorder


class EvalDatasetRecorder(EvalRecorder[EvalDataset, EvalDataset]):
    # TODO: use Dataset ability
    async def record(self, eval_input: EvalDataset, **kwargs) -> EvalDataset:
        eval_dataset = EvalDataset(**dataclasses.asdict(eval_input))
        await self.storage.create_data(eval_dataset.eval_dataset_id, eval_dataset)
        return eval_dataset

    async def get_by_key(self, key: str, **kwargs) -> EvalDataset:
        """Get the evaluation dataset.

        Args:
            key: The dataset id.

        Returns:
            EvalDataset: the eval dataset.
        """
        return await self.storage.get_data(key)


class EvalResultRecorder(EvalRecorder[EvalResult, EvalResult]):
    async def record(self, eval_input: EvalResult, **kwargs) -> EvalResult:
        await self.storage.create_data(block_id=eval_input.eval_result_id, data=eval_input, overwrite=False)
        return eval_input

    async def get_by_key(self, key: str, **kwargs) -> EvalResult:
        """Get the evaluation result.

        Args:
            key: The evaluation result id.
        """
        return await self.storage.get_data(key)


class EvalTaskRecorder(EvalRecorder[str, EvalTask]):
    async def record(self, eval_input: str = None, **kwargs) -> EvalTask:
        # eval_input is task name
        if not eval_input:
            eval_input = f"EvalTask_{self.eval_config.eval_dataset_id_or_file_path}"

        eval_task = EvalTask(config=self.eval_config, task_name=eval_input)
        await self.storage.create_data(block_id=eval_task.task_id, data=eval_task, overwrite=False)
        return eval_task

    async def get_by_key(self, key: str, **kwargs) -> EvalTask:
        """Get an evaluation task.

        Args:
            key: The task id.

        Returns:
            EvalDataset: the eval dataset.
        """
        return await self.storage.get_block(block_id=key)
