### Dataset
```python
from typing import Any, Dict, Generic, List, TypeVar, Callable
from pydantic import BaseModel, Field

_T_co = TypeVar("_T_co", covariant=True)

class Dataset(BaseModel, Generic[_T_co]):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    data: List[_T_co]
    metadata: Dict[str, Any] = Field(default_factory=dict)
    transforms: List[Callable[[_T_co], _T_co]] = Field(default_factory=list)
```

#### Dataset initialization
1) Initialize directly with a List[] as the data source:

```python
# 1) A list of strings
ds_text: Dataset[str] = Dataset(id="1", name="texts", data=["a", "b"])

# 2) A list of arbitrary Pydantic models
class DataRowModel(BaseModel):
    x: int
    y: str

ds_row: Dataset[DataRowModel] = Dataset(
    id="2",
    name="rows",
    data=[DataRowModel(x=1, y="a"), DataRowModel(x=2, y="b")],
)
```

2) Dataset supports chained transform operations. You can register multiple transform functions, which are applied in order when retrieving samples via __getitem__:

```python
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
```

```python
def score_transform(item: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(item, dict):
        passed = item.get("score", 0.0) >= 0.8
        return {"pass": passed, **item}
    return item

def add_timestamp(item: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(item, dict):
        import time
        return {"timestamp": time.time(), **item}
    return item

## Use chained transform operations
transformed_dataset = Dataset[Dict[str, Any]](
    name="transformed_dataset",
    data=[{"name":"x", "score":0.85}, {"name": "y", "k2":0.51}]
).transform(score_transform).transform(add_timestamp)

print(transformed_dataset[0])
## Output: {"name":"x", "score":0.85, "pass": True, "timestamp": 1234567890.123}
```

#### Dataset loading
+ Local files: CSV, JSON, JSONL, TXT, Parquet (source can be a single path `str` or an ordered list `List[str]`)
+ Hugging Face Hub: public datasets hosted on the Hub

```python
# Load from a local file (single path)
dataset = Dataset[Dict[str, Any]](name="my_dataset", data=[])
dataset.load_from(source="path/to/file.csv", format="csv")

# Load from multiple local files in order
dataset.load_from(
    source=["/data/a.json", "/data/b.jsonl", "/data/c.csv"],  # read sequentially
    limit=2000,               # limit is applied across files cumulatively
    preload_transform=lambda x: x
)

# Load from Hugging Face Hub
dataset.load_from(source="LucasFang/FLUX-Reason-6M", split="train")

# Apply preload_transform during loading (transform each record on the fly)
def preprocess(item):
    # Perform preprocessing while loading
    return {"processed": True, **item}

dataset.load_from(
    source="data.json",
    preload_transform=preprocess,
    limit=1000  # cap number of loaded items
)

Notes on JSON/JSONL reading behavior:
- JSON: attempts to `json.load`. If it fails, falls back to NDJSON (line-delimited) parsing.
- JSONL: reads line by line; stops early once `limit` is reached; errors include the failing line number.
```

### DataLoader
Convert the Dataset into a batch-iterable DataLoader:

1. batch_size: number of samples per batch (default 1)
2. sampler: an iterator of indices. When provided, it defines the order, ignoring `shuffle`. Built-ins: SequentialSampler, RandomSampler, BatchSampler
3. shuffle: whether to randomly shuffle sample order. Ignored if `sampler` is provided
4. drop_last: default False; if True, drop the last incomplete batch
5. seed: random seed used for shuffling
6. batch_sampler: yields batches of indices, e.g., [[1,4,2], [3,5,6]]. Mutually exclusive with `batch_size`, `shuffle`, `sampler`, `drop_last`

```python
class Dataset:
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
```

```python
# Built-in batching
for batch in dataset.to_dataloader(batch_size=32, shuffle=True):
    # Process the batch
    pass

# Using a custom sampler (iterate batches of indices)
for batch_indices in batch_sampler:
    batch = [dataset[i] for i in batch_indices]
    # Process the batch
```

#### Sampler
`aworld.dataset.sampler` provides lightweight samplers to control index generation and batch grouping:

1. **SequentialSampler(length: int)**: sequential sampling, yields `[0, 1, ..., length-1]`.
    - **length**: dataset length, non-negative integer.
    - Use cases: deterministic iteration, evaluation baselines.
2. **RandomSampler(length: int, seed: Optional[int] = None)**: random permutation without replacement.
    - **length**: dataset length.
    - **seed**: random seed for reproducibility.
    - Use cases: randomized training/evaluation with reproducibility.
3. **BatchSampler(sampler: Sampler, batch_size: int, drop_last: bool)**: wrap any base sampler into batches of indices.
    - **sampler**: base index sampler (e.g., `SequentialSampler` or `RandomSampler`).
    - **batch_size**: number of indices per batch, positive integer.
    - **drop_last**: whether to drop the last incomplete batch.
    - Output type: `Iterator[List[int]]`, each iteration yields a list of indices.

```python
from aworld.dataset.sampler import SequentialSampler, RandomSampler, BatchSampler

num_items = len(dataset.data)

# 1) Sequential sampling
seq_sampler = SequentialSampler(num_items)
print(list(seq_sampler)[:5])  # [0, 1, 2, 3, 4]

# 2) Random sampling (reproducible with seed)
rand_sampler = RandomSampler(num_items, seed=42)
print(list(rand_sampler)[:5])  # e.g. [8, 1, 5, 0, 3]

# 3) Batch sampling (based on a random sampler)
batch_sampler = BatchSampler(rand_sampler, batch_size=32, drop_last=False)
for idx_batch in batch_sampler:
    batch = [dataset[i] for i in idx_batch]
    # Process the batch
```

##### Coordination with to_dataloader and mutual exclusivity rules
`Dataset.to_dataloader` cooperates with samplers in two ways:

+ **Provide `sampler` explicitly**:
    - When `sampler` is provided, `shuffle` is ignored; ordering is fully determined by the sampler.
    - You can still specify `batch_size` and `drop_last`.
+ **Provide `batch_sampler` explicitly**:
    - When `batch_sampler` is provided, the following must remain default/not provided: `batch_size`, `shuffle`, `sampler`, `drop_last`; otherwise a ValueError is raised.
    - In this mode, `to_dataloader` directly consumes index batches produced by `batch_sampler`.

```python
# A) Use shuffle (no sampler)
for batch in dataset.to_dataloader(batch_size=16, shuffle=True, seed=123):
    ...

# B) Use a custom sampler (overrides shuffle)
sampler = RandomSampler(len(dataset.data), seed=123)
for batch in dataset.to_dataloader(batch_size=16, sampler=sampler, drop_last=True):
    ...

# C) Use batch_sampler (exclusive mode)
sampler = SequentialSampler(len(dataset.data))
batch_sampler = BatchSampler(sampler, batch_size=16, drop_last=False)
for batch in dataset.to_dataloader(batch_sampler=batch_sampler):
    ...
```

#### collate_fn
`collate_fn` is an optional function used to merge multiple samples in a batch into a single batch object. When `collate_fn` is `None`, the DataLoader returns the raw list of samples; when provided, the function is applied to each batch.

```python
from typing import List, Dict, Any

def custom_collate_fn(batch: List[Dict[str, Any]]) -> Dict[str, List[Any]]:
    """Convert a list of dicts into a dict of lists grouped by field."""
    result = {}
    for item in batch:
        for key, value in item.items():
            if key not in result:
                result[key] = []
            result[key].append(value)
    return result

# Use a custom collate_fn
dataloader = DataLoader(
    dataset,
    batch_size=4,
    collate_fn=custom_collate_fn
)

for batch in dataloader:
    # batch is now {"field1": [val1, val2, val3, val4], "field2": [...]}
    print(batch)
```

**collate_fn parameters:**

+ **Type**: `Optional[Callable[[List[_T_co]], _Batch]]`
+ **Purpose**: convert a list of samples into a batch object
+ **Default**: `None` (return the list as-is)
+ **When applied**: immediately after each batch is formed
+ **Return type**: any `_Batch` type; not limited to lists

**Common use cases:**

1. Tensor stacking: combine multiple tensor samples into a batch tensor
2. Field-wise grouping: reorganize a list of dicts into dicts of lists
3. Padding and alignment: pad variable-length sequences to the same length
4. Custom batch processing: implement specialized batching logic

```python
# Example: padding variable-length text
import torch

def pad_collate_fn(batch: List[str]) -> torch.Tensor:
    # Find the maximum length
    max_len = max(len(text) for text in batch)
    # Pad to the same length
    padded = [text.ljust(max_len) for text in batch]
    return torch.tensor([[ord(c) for c in text] for text in padded])

# Used in to_dataloader
for batch in dataset.to_dataloader(batch_size=8, collate_fn=pad_collate_fn):
    # batch is a padded tensor
    pass
```

### Summary
AWorld Dataset pipeline:

1. **Dataset**: multi-source loading, chained transforms, metadata management
2. **DataLoader**: flexible batching, sampling, and shuffling
    1. **Sampler**: sequential, random, and batch sampling strategies
    2. **collate_fn**: customizable batch merging to fit diverse scenarios
