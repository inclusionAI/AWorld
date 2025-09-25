import abc

from aworld.evaluations.base import EvalDataset, EvalDataCase
from aworld.core.storage.base import Storage
from aworld.core.storage.inmemory_store import InmemoryStorage

# TODO: use Dataset ability
class EvalDatasetManager(abc.ABC):

    @abc.abstractmethod
    async def create_eval_dataset(self, run_id: str, dataset_name: str, data_cases: list[EvalDataCase]) -> EvalDataset:
        """Create an eval dataset.

        Args:
            data_cases: the data cases.

        Returns:
            EvalDataset: the created eval dataset.
        """

    @abc.abstractmethod
    async def get_eval_dataset(self, dataset_id: str) -> EvalDataset:
        """Get an eval dataset.

        Args:
            dataset_id: the dataset id.

        Returns:
            EvalDataset: the eval dataset.
        """


class DefaultEvalDatasetManager(EvalDatasetManager):
    """Default eval dataset manager."""

    def __init__(self, storage: Storage[EvalDataset] = InmemoryStorage()):
        self.storage = storage

    async def create_eval_dataset(self, run_id: str, dataset_name: str, data_cases: list[EvalDataCase]) -> EvalDataset:
        """Create an eval dataset.

        Args:
            data_cases: the data cases.

        Returns:
            EvalDataset: the created eval dataset.
        """

        eval_dataset = EvalDataset(eval_dataset_name=dataset_name, eval_cases=data_cases, run_id=run_id)
        for data_case in eval_dataset.eval_cases:
            data_case.eval_dataset_id = eval_dataset.eval_dataset_id
            data_case.run_id = run_id
        await self.storage.create_data(eval_dataset.eval_dataset_id, eval_dataset)
        return eval_dataset

    async def get_eval_dataset(self, dataset_id: str) -> EvalDataset:
        """Get an eval dataset.

        Args:
            dataset_id: the dataset id.

        Returns:
            EvalDataset: the eval dataset.
        """
        return await self.storage.get_data(dataset_id)
