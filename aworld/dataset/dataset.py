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
        source: Union[str, List[str]],
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
        """Load data into `data` from a local path(s) or Hugging Face Hub.

        Args:
            source: Local file path (csv/json/txt/parquet), a list of local file paths
                to be loaded in order, or a Hugging Face repo id when the local path
                does not exist (e.g. "imdb", "glue").
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

        # Local path branch (single path or list of paths)
        if isinstance(source, list) or (isinstance(source, str) and os.path.exists(source)):
            paths: List[Path]
            if isinstance(source, list):
                paths = [Path(p) for p in source]
            else:
                paths = [Path(source)]

            # Validate existence when list provided
            for p in paths:
                if not p.exists():
                    raise FileNotFoundError(f"File not found: {str(p)}")

            loaded_items: List[Any] = []
            formats_seen: List[str] = []
            remaining = limit

            def _read_single_file(file_path: Path, fmt_override: Optional[str], max_items: Optional[int]) -> List[Any]:
                fmt_local = (fmt_override or file_path.suffix.lstrip(".")).lower()
                if fmt_local not in {"csv", "json", "txt", "parquet"}:
                    raise ValueError(f"Unsupported file format: {fmt_local!r}")

                if fmt_local == "csv":
                    with open(file_path, "r", encoding=encoding, newline="") as f:
                        reader = csv.DictReader(f)
                        return _apply_limit(reader, max_items)

                if fmt_local == "json":
                    with open(file_path, "r", encoding=encoding) as f:
                        try:
                            obj = json.load(f)
                        except json.JSONDecodeError:
                            f.seek(0)
                            items_local: List[Any] = []
                            for line in f:
                                line = line.strip()
                                if not line:
                                    continue
                                items_local.append(json.loads(line))
                            obj = items_local

                    if isinstance(obj, dict) and json_field is not None:
                        obj = obj.get(json_field, [])
                    if not isinstance(obj, list):
                        raise ValueError("JSON content must be a list or specify json_field to extract a list.")
                    return _apply_limit(obj, max_items)

                if fmt_local == "txt":
                    with open(file_path, "r", encoding=encoding) as f:
                        lines_iter = (line.rstrip("\n") for line in f)
                        return _apply_limit(lines_iter, max_items)

                if fmt_local == "parquet":
                    try:
                        import pandas as pd  # type: ignore
                        df = pd.read_parquet(file_path, columns=parquet_columns)
                        records_local = df.to_dict(orient="records")
                        return _apply_limit(records_local, max_items)
                    except Exception:
                        try:
                            import pyarrow.parquet as pq  # type: ignore
                            table = pq.read_table(file_path, columns=parquet_columns)
                            records_local = table.to_pylist()
                            return _apply_limit(records_local, max_items)
                        except Exception as e:  # pragma: no cover - environment dependent
                            raise RuntimeError(
                                "Failed to read parquet file. Ensure pandas or pyarrow is installed."
                            ) from e

                # Unreachable
                return []

            for p in paths:
                fmt_this = (format or p.suffix.lstrip(".")).lower()
                formats_seen.append(fmt_this if fmt_this else "")
                max_items_for_this = remaining
                items_this = _read_single_file(p, format, max_items_for_this)
                if preload_transform is not None:
                    items_this = [preload_transform(it) for it in items_this]  # type: ignore[misc]
                loaded_items.extend(items_this)
                if limit is not None:
                    remaining = max(0, limit - len(loaded_items))
                    if remaining == 0:
                        break

            self.data = loaded_items  # type: ignore[assignment]
            meta: Dict[str, Any] = {"format": "multiple" if len(set(formats_seen)) > 1 else (formats_seen[0] or "")}
            if len(paths) == 1:
                meta.update({"source": str(paths[0])})
            else:
                meta.update({"sources": [str(p) for p in paths]})
            self.metadata.update(meta)
            return

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







