import abc
from aworld.evaluations.base import EvalResult
from aworld.core.storage.base import Storage
from aworld.core.storage.inmemory_store import InmemoryStorage


class EvalResultRecorder(abc.ABC):

    @abc.abstractmethod
    async def save_eval_result(self, eval_result: EvalResult) -> EvalResult:
        """save the evaluation result.

        Args:
            eval_result: the evaluation result.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def get_eval_result(self, eval_result_id: str) -> EvalResult:
        """get the evaluation result.

        Args:
            eval_result_id: the evaluation result id.

        Returns:
            eval_result: the evaluation result.
        """
        raise NotImplementedError


class DefaultEvalResultRecorder(EvalResultRecorder):

    def __init__(self, storage: Storage = None):
        self.storage = storage or InmemoryStorage()

    async def save_eval_result(self, eval_result: EvalResult) -> None:
        """save the evaluation result.

        Args:
            eval_result: the evaluation result.
        """
        await self.storage.create_data(block_id=eval_result.eval_result_id, data=eval_result, overwrite=False)
        return eval_result

    async def get_eval_result(self, eval_result_id: str) -> EvalResult:
        """get the evaluation result.

        Args:
            eval_result_id: the evaluation result id.

        Returns:
            eval_result: the evaluation result.
        """
        return await self.storage.get_data(eval_result_id)
