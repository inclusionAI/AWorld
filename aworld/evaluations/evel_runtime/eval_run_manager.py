import abc
from aworld.core.storage.base import Storage
from aworld.core.storage.inmemory import InMemoryStorage
from aworld.evaluations.base import EvalRunConfig, EvalRun


class EvalRunManager(abc.ABC):
    '''
    Base class of evaluation run manager.
    '''

    @abc.abstractmethod
    async def create_eval_run(self, eval_config: EvalRunConfig, eval_run_name: str = None) -> EvalRun:
        """Create an evaluation run.

        Returns:
            EvalRun
        """

    @abc.abstractmethod
    async def get_eval_run(self, eval_run_id: str) -> EvalRun:
        """Get an evaluation run.

        Returns:
            EvalRun
        """


class DefaultEvalRunManager(EvalRunManager):
    '''
    Default evaluation run manager.
    '''

    def __init__(self, storage: Storage[EvalRun] = InMemoryStorage):
        self.storage = storage

    async def create_eval_run(self, eval_config: EvalRunConfig, eval_run_name: str = None) -> EvalRun:
        if not eval_run_name:
            eval_run_name = f"EvalRun_{eval_config.eval_dataset_id_or_file_path}"
        eval_run = EvalRun(config=eval_config, run_name=eval_run_name)
        await self.storage.create_block(block_id=eval_run.run_id, data=eval_run, overwrite=False)
        return eval_run

    async def get_eval_run(self, eval_run_id: str) -> EvalRun:
        return await self.storage.get_block(block_id=eval_run_id)
