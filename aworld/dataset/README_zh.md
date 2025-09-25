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

#### Dataset 初始化
1）在初始化时直接传入List[]，作为data数据：

```python
# 1) 列表里是字符串
ds_text: Dataset[str] = Dataset(id="1", name="texts", data=["a", "b"])

# 2) 列表里是任意 Pydantic 模型
class DataRowModel(BaseModel):
    x: int
    y: str

ds_row: Dataset[DataRowModel] = Dataset(
    id="2",
    name="rows",
    data=[DataRowModel(x=1, y="a"), DataRowModel(x=2, y="b")],
)
```

2）Dataset支持链式transform操作，可以注册多个transform函数，在__getitem__获取数据样本时按顺序应用：：

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
        pass = item.get("score", 0.0) >= 0.8
        return {"pass":pass, **item}
    return item

def add_timestamp(item: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(item, dict):
        import time
        return {"timestamp": time.time(), **item}
    return item

## 使用链式transform操作
transformed_dataset = Dataset[Dict[str, Any]](
    name="transformed_dataset",
    data=[{"name":"x", "score":0.85}, {"name": "y", "k2":0.51}]
).transform(score_transform).transform(add_timestamp)

print(transformed_dataset[0])
## 输出： {"name":"x", "score":0.85, "pass": True, "timestamp": 1234567890.123}
```

#### Dataset 加载
+ 本地文件: CSV, JSON, JSONL, TXT, Parquet（`source` 可以是单个文件路径 `str`，也可以是一个文件路径的列表 `List[str]`）
+ HuggingFace Hub: Huggingface上公开的数据集

```python
# 从本地文件加载（单个文件）
dataset = Dataset[Dict[str, Any]](name="my_dataset", data=[])
dataset.load_from(source="path/to/file.csv", format="csv")

# 按顺序从多个本地文件加载
dataset.load_from(
    source=["/data/a.json", "/data/b.jsonl", "/data/c.csv"],  # 按顺序读取
    limit=2000,                 # limit 在多文件间累计生效
    preload_transform=lambda x: x
)

# 从 Hugging Face 加载
dataset.load_from(source="LucasFang/FLUX-Reason-6M", split="train")

# 加载时应用preload_transform（在加载过程中直接转换数据）
def preprocess(item):
    # 在加载时就进行数据预处理
    return {"processed": True, **item}

dataset.load_from(
    source="data.json", 
    preload_transform=preprocess,
    limit=1000  # 限制加载数量
)

关于 JSON/JSONL 的读取行为：
- JSON：优先使用 `json.load`，若失败则退化为逐行（NDJSON）解析。
- JSONL：逐行解析；到达 `limit` 会提前停止；解析失败会报告出错行号，便于定位。
```

### DataLoader
将Dataset中的数据转换成批量可迭代的dataloader：

1. batch_size：指定每个batch的样本数量，默认为1
2. sampler：返回索引的迭代器，按照sampler的实现逻辑来输出样本，内置实现SequentialSampler, RandomSampler, BatchSampler
3. shuffle：是否随机打乱样本顺序，如果指定了sampler，则忽略shuffle参数，按照sampler的索引输出样本
4. drop_last：默认False，如果设置为True，将丢弃最后一个不足batch_size的batch
5. seed：shuffle中使用的随机种子
6. batch_sampler：返回的索引是按批次返回的，指定每个batch返回的样本下标，如[[1,4,2], [3,5,6]]，则表示第一个batch按顺序返回下标为1、4、2的样本

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
# 使用内置批处理
for batch in dataset.to_dataloader(batch_size=32, shuffle=True):
    # 处理批次数据
    # 指定batch_size，shuffle随机打乱顺序
    pass

# 使用自定义采样器
for batch_indices in batch_sampler:
    batch = [dataset[i] for i in batch_indices]
    # 处理批次数据
```

#### Sampler
`aworld.dataset.sampler` 提供了轻量级采样器以控制样本索引的产生与批次划分：

1. **SequentialSampler(length: int)**: 顺序采样，产生 `[0, 1, ..., length-1]`。
    - **length**: 数据集长度，必须为非负整数。
    - 适用场景：严格顺序遍历、评测对比。
2. **RandomSampler(length: int, seed: Optional[int] = None)**: 无放回随机采样，打乱 `[0, ..., length-1]`。
    - **length**: 数据集长度。
    - **seed**: 随机种子，可保证可复现的顺序。
    - 适用场景：随机训练/评测，需可复现时传入 `seed`。
+ **BatchSampler(sampler: Sampler, batch_size: int, drop_last: bool)**: 将任意基础采样器封装成“批索引”采样器。
    - **sampler**: 基础索引采样器（如 `SequentialSampler` 或 `RandomSampler`）。
    - **batch_size**: 每批的索引数量，必须为正整数。
    - **drop_last**: 是否丢弃最后一个不完整批次。
    - 产出类型：`Iterator[List[int]]`，每次迭代返回一个索引列表。

```python
from aworld.dataset.sampler import SequentialSampler, RandomSampler, BatchSampler

num_items = len(dataset.data)

# 1) 顺序采样
seq_sampler = SequentialSampler(num_items)
print(list(seq_sampler)[:5])  # [0, 1, 2, 3, 4]

# 2) 随机采样（传入seed可复现）
rand_sampler = RandomSampler(num_items, seed=42)
print(list(rand_sampler)[:5])  # 例如 [8, 1, 5, 0, 3]

# 3) 批采样（基于随机采样器）
batch_sampler = BatchSampler(rand_sampler, batch_size=32, drop_last=False)
for idx_batch in batch_sampler:
    batch = [dataset[i] for i in idx_batch]
    # 处理批次数据
```

##### 与 to_dataloader 的配合与互斥规则
`Dataset.to_dataloader` 提供了与采样器配合的两种方式：

+ **显式传入 **`sampler`：
    - 当 `sampler` 被提供时，`shuffle` 参数被忽略，数据顺序完全由 `sampler` 决定。
    - 可同时指定 `batch_size`、`drop_last`。
+ **显式传入 **`batch_sampler`：
    - 当提供 `batch_sampler` 时，以下参数必须保持默认或未提供：`batch_size`、`shuffle`、`sampler`、`drop_last`；否则会抛出错误。
    - 这种模式下，`to_dataloader` 直接按 `batch_sampler` 产生的索引批次取数据。

```python
# A) 使用 shuffle（无 sampler）
for batch in dataset.to_dataloader(batch_size=16, shuffle=True, seed=123):
    ...

# B) 使用自定义 sampler（覆盖 shuffle）
sampler = RandomSampler(len(dataset.data), seed=123)
for batch in dataset.to_dataloader(batch_size=16, sampler=sampler, drop_last=True):
    ...

# C) 使用 batch_sampler（独占模式）
sampler = SequentialSampler(len(dataset.data))
batch_sampler = BatchSampler(sampler, batch_size=16, drop_last=False)
for batch in dataset.to_dataloader(batch_sampler=batch_sampler):
    ...
```

#### collate_fn
`collate_fn` 是一个可选的函数，用于将batch中的多个样本合并成一个batch对象。当 `collate_fn` 为 `None` 时，DataLoader 直接返回样本列表；当提供 `collate_fn` 时，会对每个batch应用该函数。

```python
from typing import List, Dict, Any

def custom_collate_fn(batch: List[Dict[str, Any]]) -> Dict[str, List[Any]]:
    """将字典列表转换为按字段分组的字典"""
    result = {}
    for item in batch:
        for key, value in item.items():
            if key not in result:
                result[key] = []
            result[key].append(value)
    return result

# 使用自定义collate_fn
dataloader = DataLoader(
    dataset,
    batch_size=4,
    collate_fn=custom_collate_fn
)

for batch in dataloader:
    # batch 现在是 {"field1": [val1, val2, val3, val4], "field2": [...]}
    print(batch)
```

**collate_fn 参数说明：**

+ **类型**: `Optional[Callable[[List[_T_co]], _Batch]]`
+ **作用**: 将样本列表转换为批次对象
+ **默认值**: `None`（直接返回样本列表）
+ **应用时机**: 在每个批次生成后立即应用
+ **返回值**: 可以是任意类型 `_Batch`，不限于列表

**常见使用场景：**

1. **张量堆叠**: 将多个张量样本堆叠成批次张量
2. **字段分组**: 将字典列表按字段重新组织
3. **填充对齐**: 对不同长度的序列进行填充对齐
4. **自定义批处理**: 实现特殊的批处理逻辑

```python
# 示例：处理变长文本的填充
import torch

def pad_collate_fn(batch: List[str]) -> torch.Tensor:
    # 找到最大长度
    max_len = max(len(text) for text in batch)
    # 填充到相同长度
    padded = [text.ljust(max_len) for text in batch]
    return torch.tensor([[ord(c) for c in text] for text in padded])

# 在to_dataloader中使用
for batch in dataset.to_dataloader(batch_size=8, collate_fn=pad_collate_fn):
    # batch 现在是填充后的张量
    pass
```

### 总结
AWorld Dataset 数据处理：

1. **Dataset**: 支持多种数据源加载、链式transform操作、元数据管理
2. **DataLoader**: 提供灵活的批处理、采样、随机化功能
    1. **Sampler**: 提供顺序、随机、批处理等多种采样策略
    2. **collate_fn**: 支持自定义批处理逻辑，满足不同场景需求





