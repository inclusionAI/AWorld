import traceback
import uuid
import random
import os
import json
import csv
from pathlib import Path
from typing import TypeVar, Generic, Dict, List, Any, Iterator, Optional, Iterable, Sized, Callable, Union

from pydantic import BaseModel, Field

from aworld.dataset.sampler import Sampler
from aworld.dataset.dataloader import DataLoader
from aworld.logs.util import logger

_T_co = TypeVar("_T_co", covariant=True)

class Dataset(BaseModel, Generic[_T_co]):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    data: List[_T_co]
    metadata: Dict[str, Any] = Field(default_factory=dict)
    transforms: List[Callable[[_T_co], _T_co]] = Field(default_factory=list)

    def transform(self, fn: Callable[[_T_co], _T_co]) -> "Dataset[_T_co]":
        """Register a transform step to be applied in order and return self for chaining."""
        self.transforms.append(fn)
        return self

    def clear_transforms(self) -> None:
        """Clear all registered transforms."""
        self.transforms.clear()

    def __getitem__(self, index) -> _T_co:
        item = self.data[index]
        if not self.transforms:
            return item
        for fn in self.transforms:
            item = fn(item)
        return item

    def __len__(self) -> int:
        return len(self.data)

    def load_from(
        self,
        source: str,
        *,
        format: Optional[str] = None,
        split: Optional[str] = None,
        subset: Optional[str] = None,
        json_field: Optional[str] = None,
        parquet_columns: Optional[List[str]] = None,
        encoding: str = "utf-8",
        limit: Optional[int] = None,
        preload_transform: Optional[Callable[[_T_co], _T_co]] = None,
    ):
        """Load data into `data` from a local path or Hugging Face Hub.

        Args:
            source: Local file path (csv/json/txt/parquet) or a Hugging Face repo id
                when the path does not exist locally (e.g. "imdb", "glue").
            format: Explicit format override for local files: "csv", "json",
                "txt", or "parquet". If omitted, inferred from file suffix.
            split: Dataset split when loading from Hugging Face (e.g. "train").
            subset: Dataset subset/config name for Hugging Face datasets.
            json_field: When loading JSON that contains a top-level object with a list
                field, specify the field name to extract.
            parquet_columns: Optional column whitelist when reading parquet.
            encoding: Text encoding for csv/json/txt.
            limit: Max number of rows/items to load (useful to cap memory).
            preload_transform: Optional callable to transform each data item while
                loading. This materializes transformed data into
                `self.data`. 

        Returns:
            self (with `data` replaced by the loaded records/items).
        """

        def _apply_limit(seq: Iterable[Any], max_items: Optional[int]) -> List[Any]:
            if max_items is None:
                return list(seq)
            out: List[Any] = []
            for i, item in enumerate(seq):
                if i >= max_items:
                    break
                out.append(item)
            return out

        def _apply_preload_transform(data: List[Any], transform_func: Optional[Callable[[_T_co], _T_co]]) -> List[_T_co]:
            if transform_func is None:
                return data  # type: ignore[return-value]
            return [transform_func(item) for item in data]  # type: ignore[misc]

        # Local path branch
        if os.path.exists(source):
            path = Path(source)
            fmt = (format or path.suffix.lstrip(".")).lower()
            if fmt not in {"csv", "json", "txt", "parquet"}:
                raise ValueError(f"Unsupported file format: {fmt!r}")

            if fmt == "csv":
                with open(path, "r", encoding=encoding, newline="") as f:
                    reader = csv.DictReader(f)
                    records = _apply_limit(reader, limit)
                self.data = _apply_preload_transform(list(records), preload_transform)  # type: ignore[assignment]
                self.metadata.update({"source": str(path), "format": "csv"})
                return

            if fmt == "json":
                with open(path, "r", encoding=encoding) as f:
                    try:
                        obj = json.load(f)
                    except json.JSONDecodeError:
                        # Fallback: newline-delimited JSON
                        f.seek(0)
                        items: List[Any] = []
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            items.append(json.loads(line))
                        obj = items

                # If a field is specified, extract it when a dict is loaded
                if isinstance(obj, dict) and json_field is not None:
                    obj = obj.get(json_field, [])
                if not isinstance(obj, list):
                    raise ValueError("JSON content must be a list or specify json_field to extract a list.")
                self.data = _apply_preload_transform(_apply_limit(obj, limit), preload_transform)  # type: ignore[assignment]
                self.metadata.update({"source": str(path), "format": "json"})
                return

            if fmt == "txt":
                with open(path, "r", encoding=encoding) as f:
                    lines = (line.rstrip("\n") for line in f)
                    self.data = _apply_preload_transform(_apply_limit(lines, limit), preload_transform)  # type: ignore[assignment]
                self.metadata.update({"source": str(path), "format": "txt"})
                return

            if fmt == "parquet":
                # Prefer pandas if available, otherwise try pyarrow directly
                try:
                    import pandas as pd  # type: ignore
                    df = pd.read_parquet(path, columns=parquet_columns)
                    records = df.to_dict(orient="records")
                    self.data = _apply_preload_transform(_apply_limit(records, limit), preload_transform)  # type: ignore[assignment]
                    self.metadata.update({"source": str(path), "format": "parquet"})
                    return
                except Exception:
                    try:
                        import pyarrow.parquet as pq  # type: ignore
                        table = pq.read_table(path, columns=parquet_columns)
                        records = table.to_pylist()
                        self.data = _apply_preload_transform(_apply_limit(records, limit), preload_transform)  # type: ignore[assignment]
                        self.metadata.update({"source": str(path), "format": "parquet"})
                        return
                    except Exception as e:  # pragma: no cover - environment dependent
                        raise RuntimeError(
                            "Failed to read parquet file. Ensure pandas or pyarrow is installed."
                        ) from e

        # Hugging Face Hub branch (requires `datasets` library)
        try:
            from datasets import load_dataset  # type: ignore
        except Exception as e:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "Loading from Hugging Face Hub requires the `datasets` library."
            ) from e

        if split is None:
            split = "train"

        try:
            ds = load_dataset(source, subset, split=split, streaming=False)  # type: ignore[call-arg]
            # Convert to list of dicts/records
            iterator: Iterable[Any]
            try:
                iterator = ds  # pyright: ignore[reportGeneralTypeIssues]
            except Exception:
                iterator = list(ds)

            self.data = _apply_preload_transform(_apply_limit(iterator, limit), preload_transform)  # type: ignore[assignment]
            self.metadata.update({"source": source, "format": "huggingface", "split": split, "subset": subset})
            return
        except Exception as e:
            logger.warn(f"Failed to load dataset from {source}: {str(e)}.\n{traceback.format_exc()}")

    def to_dataloader(
        self,
        batch_size: Optional[int] = 1,
        sampler: Union[Sampler, Iterable, None] = None,
        shuffle: bool = False,
        drop_last: bool = False,
        seed: Optional[int] = None,
        batch_sampler: Optional[Iterable[List[int]]] = None,
    ) -> Iterator[List[_T_co]]:
        """A lightweight DataLoader-like iterator.

        Args:
            batch_size: Number of samples per batch (must be >= 1). Mutually exclusive
                with `batch_sampler`. When `batch_sampler` is provided, this must be None.
            sampler: Iterable or iterator of indices to draw samples from. If provided,
                it defines the exact ordering and selection of indices, and `shuffle` is ignored.
            shuffle: Whether to randomly shuffle the dataset indices (ignored when
                `sampler` is provided).
            drop_last: If True, drop the last incomplete batch.
            seed: Optional seed for deterministic shuffling.
            batch_sampler: Iterable yielding lists of indices per batch. Mutually exclusive
                with `batch_size`, `shuffle`, `sampler`, and `drop_last`.

        Yields:
            List of samples of length `batch_size` (except possibly the last one
            when `drop_last` is False).
        """
        loader: DataLoader[_T_co] = DataLoader(
            self,
            batch_size=batch_size,
            sampler=sampler,  # type: ignore[arg-type]
            shuffle=shuffle,
            drop_last=drop_last,
            seed=seed,
            batch_sampler=batch_sampler,
            collate_fn=None,
        )
        return iter(loader)







