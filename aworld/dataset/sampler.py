import uuid
import random
from typing import TypeVar, Generic, Dict, List, Any, Iterator, Optional, Iterable, Sized



class Sampler():
    """Base class for simplified Samplers.

    Subclasses must implement `__iter__` to yield indices. Implementing `__len__`
    is optional but recommended when the sampler size is known.
    """

    def __iter__(self) -> Iterator[int]:
        raise NotImplementedError

    # Intentionally do not provide a default __len__


class SequentialSampler(Sampler):
    """Samples elements sequentially from 0 to length-1."""

    def __init__(self, length: int) -> None:
        if length < 0:
            raise ValueError("length must be non-negative")
        self.length = length

    def __iter__(self) -> Iterator[int]:
        return iter(range(self.length))

    def __len__(self) -> int:
        return self.length


class RandomSampler(Sampler):
    """Samples elements randomly without replacement.

    Args:
        length: Total number of indices [0, length).
        seed: Optional seed for deterministic sampling.
    """

    def __init__(self, length: int, seed: Optional[int] = None) -> None:
        if length < 0:
            raise ValueError("length must be non-negative")
        self.length = length
        self.seed = seed

    def __iter__(self) -> Iterator[int]:
        indices = list(range(self.length))
        rng = random.Random(self.seed)
        rng.shuffle(indices)
        return iter(indices)

    def __len__(self) -> int:
        return self.length


class BatchSampler(Sampler):
    """Wraps another sampler to yield batches of indices.

    Args:
        sampler: Base index sampler.
        batch_size: Number of indices per batch.
        drop_last: Drop the last incomplete batch if True.
    """

    def __init__(self, sampler: Sampler, batch_size: int, drop_last: bool) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        if not isinstance(drop_last, bool):
            raise ValueError("drop_last must be a boolean")
        self.sampler = sampler
        self.batch_size = batch_size
        self.drop_last = drop_last

    def __iter__(self) -> Iterator[List[int]]:
        batch: List[int] = []
        for idx in self.sampler:
            batch.append(idx)
            if len(batch) == self.batch_size:
                yield batch
                batch = []
        if batch and not self.drop_last:
            yield batch

    # __len__ is optional; provide when underlying sampler has __len__
    def __len__(self) -> int:  # type: ignore[override]
        if hasattr(self.sampler, "__len__"):
            sampler_len = len(self.sampler)  # type: ignore[arg-type]
            if self.drop_last:
                return sampler_len // self.batch_size
            return (sampler_len + self.batch_size - 1) // self.batch_size
        raise TypeError("Length is not defined for the underlying sampler")