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
    def set_length(self, length: int) -> None:
        """Optional hook to inject dataset length at runtime.

        Default implementation stores the value on the instance so subclasses
        that rely on `self.length` can use it if they wish.
        """
        if not isinstance(length, int) or length < 0:
            raise ValueError("length must be a non-negative integer")
        self.length = length  # type: ignore[attr-defined]

    def set_dataset(self, dataset: Sized) -> None:
        """Optional hook to inject dataset at runtime.

        Stores the dataset on the instance (as `self.dataset`) and also derives
        and sets `self.length` using ``len(dataset)`` for samplers that rely on
        a numeric length.
        """
        self.dataset = dataset  # type: ignore[attr-defined]
        derived_length = len(dataset)
        self.set_length(derived_length)


class SequentialSampler(Sampler):
    """Samples elements sequentially from 0 to length-1."""

    def __init__(self, length: Optional[int] = None) -> None:
        if length is not None and length < 0:
            raise ValueError("length must be non-negative")
        self.length = length

    def __iter__(self) -> Iterator[int]:
        # Prefer explicit length; fall back to injected dataset length
        effective_length: Optional[int] = getattr(self, "length", None)
        if effective_length is None:
            effective_length = len(self.dataset)  # type: ignore[attr-defined]
        return iter(range(effective_length))

    def __len__(self) -> int:
        if getattr(self, "length", None) is not None:
            return int(self.length)  # type: ignore[return-value]
        if hasattr(self, "dataset"):
            return len(self.dataset)  # type: ignore[attr-defined]
        raise TypeError("Length is not defined for SequentialSampler")



class FixedSampler(Sampler):
    """Samples elements from a specified index.
    """

    def __init__(
            self,
            ids: list[int]
    ) -> None:
        self.ids = ids

    def set_length(self, length: int) -> None:
        super().set_length(len(self.ids))

    def __iter__(self) -> Iterator[int]:
        return iter([_ - 1 for _ in self.ids])

    def __len__(self) -> int:
        return len(self.ids)


class RandomSampler(Sampler):
    """Samples elements randomly without replacement.

    Args:
        length: Total number of indices [0, length).
        seed: Optional seed for deterministic sampling.
    """

    def __init__(self, length: Optional[int] = None, seed: Optional[int] = None) -> None:
        if length is not None and length < 0:
            raise ValueError("length must be non-negative")
        self.length = length
        self.seed = seed

    def __iter__(self) -> Iterator[int]:
        effective_length: Optional[int] = getattr(self, "length", None)
        if effective_length is None:
            effective_length = len(self.dataset)  # type: ignore[attr-defined]
        indices = list(range(effective_length))
        rng = random.Random(self.seed)
        rng.shuffle(indices)
        return iter(indices)

    def __len__(self) -> int:
        if getattr(self, "length", None) is not None:
            return int(self.length)  # type: ignore[return-value]
        if hasattr(self, "dataset"):
            return len(self.dataset)  # type: ignore[attr-defined]
        raise TypeError("Length is not defined for RandomSampler")


class RangeSampler(Sampler):
    """Samples elements from a specified range of indices.

    Supports deferred dataset length injection via `set_length`.
    """

    def __init__(
        self,
        length: Optional[int] = None,
        start_index: int = 0,
        end_index: Optional[int] = None,
        shuffle: bool = False,
        seed: Optional[int] = None,
    ) -> None:
        if length is not None and length < 0:
            raise ValueError("length must be non-negative")
        if start_index < 0:
            raise ValueError("start_index must be non-negative")
        if not isinstance(shuffle, bool):
            raise ValueError("shuffle must be a boolean")

        self.length: Optional[int] = length
        self.start_index = start_index
        self.end_index: Optional[int] = end_index
        self.shuffle = shuffle
        self.seed = seed

        # If both length and end_index are provided at init, basic validation
        if self.length is not None:
            if self.start_index >= self.length:
                raise ValueError("start_index must be < length")
            if self.end_index is None:
                self.end_index = self.length
            if self.end_index < self.start_index:
                raise ValueError("end_index must be >= start_index")

    def set_length(self, length: int) -> None:
        super().set_length(length)
        # When length is injected later, complete validations and defaults
        assert self.length is not None
        if self.start_index >= self.length:
            raise ValueError("start_index must be < length")
        if self.end_index is None:
            self.end_index = self.length
        if self.end_index < self.start_index:
            raise ValueError("end_index must be >= start_index")

    def _effective_bounds(self) -> List[int]:
        if self.end_index is None and self.length is None:
            raise ValueError("RangeSampler requires `end_index` or injected `length` before iteration")
        end = self.end_index if self.end_index is not None else self.length  # type: ignore[union-attr]
        assert end is not None
        max_len = self.length if self.length is not None else end
        eff_end = min(end, max_len)
        return [self.start_index, eff_end]

    def __iter__(self) -> Iterator[int]:
        start, eff_end = self._effective_bounds()
        if start < 0:
            raise ValueError("start_index must be non-negative")
        if eff_end < start:
            raise ValueError("end_index must be >= start_index")
        indices = list(range(start, eff_end))
        if self.shuffle and len(indices) > 1:
            rng = random.Random(self.seed)
            rng.shuffle(indices)
        return iter(indices)

    def __len__(self) -> int:
        start, eff_end = self._effective_bounds()
        return max(0, eff_end - start)


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