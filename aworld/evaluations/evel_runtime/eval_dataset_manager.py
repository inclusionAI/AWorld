import abc

from aworld.evaluations.base import EvalDataset, EvalDataCase
from aworld.core.storage.base import Storage
from aworld.core.storage.inmemory import InMemoryStorage


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

    def __init__(self,  storage: Storage[EvalDataset] = InMemoryStorage):
        pass

    async def create_eval_dataset(self, run_id: str, dataset_name: str, data_cases: list[EvalDataCase]) -> EvalDataset:
        """Create an eval dataset.

        Args:
            data_cases: the data cases.

        Returns:
            EvalDataset: the created eval dataset.
        """

        eval_dataset = EvalDataset(eval_dataset_name=dataset_name, eval_cases=data_cases, run_id=run_id)
        storage.c
