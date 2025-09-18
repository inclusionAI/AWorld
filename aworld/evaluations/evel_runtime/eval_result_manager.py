
import abc
from aworld.evaluations.base import EvalResult
from aworld.core.storage.base import Storage
from aworld.core.storage.inmemory import InMemoryStorage


class EvalResultManager(abc.ABC):
    '''
    The base class of eval result manager.
    '''

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


class DefaultEvalResultManager(EvalResultManager):
    '''
    The default eval result manager.
    '''

    def __init__(self, storage: Storage = None):
        self.storage = storage or InMemoryStorage()

    async def save_eval_result(self, eval_result: EvalResult) -> None:
        """save the evaluation result.

        Args:
            eval_result: the evaluation result.
        """
        await self.storage.put(eval_result.eval_result_id, eval_result)
        return eval_result

    async def get_eval_result(self, eval_result_id: str) -> EvalResult:
        """get the evaluation result.

        Args:
            eval_result_id: the evaluation result id.

        Returns:
            eval_result: the evaluation result.
        """
        return await self.storage.get(eval_result_id)
