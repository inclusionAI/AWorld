import abc
from aworld.core.storage.base import Storage
from aworld.core.storage.inmemory_store import InmemoryStorage
from aworld.evaluations.base import EvalTask
from aworld.config.conf import EvaluationConfig


class EvalTaskRecorder(abc.ABC):

    @abc.abstractmethod
    async def create_eval_task(self, eval_config: EvaluationConfig, eval_run_name: str = None) -> EvalTask:
        """Create an evaluation run.

        Returns:
            EvalTask
        """

    @abc.abstractmethod
    async def get_eval_task(self, eval_run_id: str) -> EvalTask:
        """Get an evaluation run.

        Returns:
            EvalTask
        """


class DefaultEvalTaskRecorder(EvalTaskRecorder):
    '''
    Default evaluation run manager.
    '''

    def __init__(self, storage: Storage[EvalTask] = InmemoryStorage()):
        self.storage = storage

    async def create_eval_task(self, eval_config: EvaluationConfig, eval_run_name: str = None) -> EvalTask:
        if not eval_run_name:
            eval_run_name = f"EvalTask_{eval_config.eval_dataset_id_or_file_path}"
        eval_run = EvalTask(config=eval_config, run_name=eval_run_name)
        await self.storage.create_data(block_id=eval_run.run_id, data=eval_run, overwrite=False)
        return eval_run

    async def get_eval_task(self, eval_run_id: str) -> EvalTask:
        return await self.storage.get_block(block_id=eval_run_id)
