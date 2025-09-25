import random
from typing import Generic, Iterable, Iterator, List, Optional, TypeVar, Callable, Union, Any

from aworld.dataset.sampler import Sampler


_T_co = TypeVar("_T_co", covariant=True)
_Batch = TypeVar("_Batch")


class DataLoader(Generic[_T_co]):
    """A lightweight, framework-agnostic DataLoader.

    Args:
        dataset: Sequence-like object that supports ``__len__`` and ``__getitem__``.
        batch_size: Number of samples per batch (>=1). Mutually exclusive with ``batch_sampler``.
        sampler: Iterable of indices. If provided, overrides ``shuffle``.
        shuffle: Shuffle indices when ``sampler`` is not provided.
        drop_last: Drop the last incomplete batch.
        seed: Optional seed for deterministic shuffling.
        batch_sampler: Iterable yielding lists of indices per batch. Mutually exclusive with
            ``batch_size``, ``shuffle``, ``sampler``, and ``drop_last``.
        collate_fn: Optional function to merge a list of samples into a batch object.
    """

    def __init__(
        self,
        dataset: Any,
        *,
        batch_size: Optional[int] = 1,
        sampler: Union[Sampler, Iterable[int], None] = None,
        shuffle: bool = False,
        drop_last: bool = False,
        seed: Optional[int] = None,
        batch_sampler: Optional[Iterable[List[int]]] = None,
        collate_fn: Optional[Callable[[List[_T_co]], _Batch]] = None,
    ) -> None:
        # Validate exclusivity
        if batch_sampler is not None:
            if batch_size is not None or shuffle or sampler is not None or drop_last:
                raise ValueError(
                    "batch_sampler is mutually exclusive with batch_size, shuffle, sampler, and drop_last"
                )
        else:
            if batch_size is None or batch_size <= 0:
                raise ValueError("batch_size must be a positive integer")

        self.dataset = dataset
        self.batch_size = batch_size
        self.sampler = sampler
        self.shuffle = shuffle
        self.drop_last = drop_last
        self.seed = seed
        self.batch_sampler = batch_sampler
        self.collate_fn = collate_fn

    def __iter__(self) -> Iterator[Union[List[_T_co], _Batch]]:
        # If batch_sampler is provided, try to inject dataset length then iterate directly on its batches
        if self.batch_sampler is not None:
            # Best-effort: if batch_sampler wraps a sampler with set_length, inject length
            try:
                inner_sampler = getattr(self.batch_sampler, "sampler", None)
                if inner_sampler is not None:
                    if hasattr(inner_sampler, "set_dataset"):
                        inner_sampler.set_dataset(self.dataset)  # type: ignore[call-arg]
                    elif hasattr(inner_sampler, "set_length") and inner_sampler.length is None:
                        inner_sampler.set_length(len(self.dataset))  # type: ignore[call-arg]
            except Exception:
                pass
            for batch_indices in self.batch_sampler:
                batch: List[_T_co] = []
                for idx in batch_indices:
                    try:
                        item = self.dataset.__getitem__(idx)  # type: ignore[attr-defined]
                    except NotImplementedError:
                        item = self.dataset.data[idx]  # type: ignore[attr-defined]
                    batch.append(item)
                yield self._maybe_collate(batch)
            return

        # Resolve indices from sampler / shuffle
        num_items = len(self.dataset)
        if self.sampler is not None:
            # Inject dataset length for samplers that support it
            try:
                if hasattr(self.sampler, "set_dataset"):
                    self.sampler.set_dataset(self.dataset)  # type: ignore[call-arg]
                elif hasattr(self.sampler, "set_length") and self.sampler.length is None:
                    self.sampler.set_length(num_items)  # type: ignore[call-arg]
            except Exception:
                pass
            indices = list(iter(self.sampler))
        else:
            indices = list(range(num_items))
            if self.shuffle and num_items > 1:
                rng = random.Random(self.seed)
                rng.shuffle(indices)

        # Batch iteration
        assert self.batch_size is not None
        batch: List[_T_co] = []
        for idx in indices:
            try:
                item = self.dataset.__getitem__(idx)  # type: ignore[attr-defined]
            except NotImplementedError:
                item = self.dataset.data[idx]  # type: ignore[attr-defined]
            batch.append(item)
            if len(batch) == self.batch_size:
                yield self._maybe_collate(batch)
                batch = []

        if batch and not self.drop_last:
            yield self._maybe_collate(batch)

    def __len__(self) -> int:
        if self.batch_sampler is not None:
            # Try best-effort length when batch_sampler has __len__
            if hasattr(self.batch_sampler, "__len__"):
                return len(self.batch_sampler)  # type: ignore[arg-type]
            raise TypeError("Length is not defined for the provided batch_sampler")

        num_items = len(self.dataset)
        assert self.batch_size is not None
        if self.drop_last:
            return num_items // self.batch_size
        return (num_items + self.batch_size - 1) // self.batch_size

    def _maybe_collate(self, batch: List[_T_co]) -> Union[List[_T_co], _Batch]:
        if self.collate_fn is None:
            return batch
        return self.collate_fn(batch)


