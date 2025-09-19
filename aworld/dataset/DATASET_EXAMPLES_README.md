# Dataset 使用示例

基于 `simplified_dataset.py` 的完整使用示例集合，展示了 Dataset 类的各种功能。

## 文件说明

### 1. `dataset_example.py` - 完整功能演示
这是最全面的示例，包含以下功能：

- **本地 CSV 文件加载**: 从 `~/Downloads/hotpot_qa_validation.csv` 加载数据
- **Hugging Face 数据集加载**: 从 `LucasFang/FLUX-Reason-6M` 加载数据
- **基本数据访问**: 索引访问、数据预览
- **采样器演示**: 顺序采样器、随机采样器
- **批处理功能**: `to_dataloader` 方法、自定义批处理采样器
- **数据变换**: 自定义变换函数
- **元数据管理**: 数据集元数据的添加和查看
- **数据集统计**: 长度、字段信息等

### 2. `simple_dataset_demo.py` - 简化演示
这是一个自包含的示例，不需要外部文件：

- **创建示例 CSV**: 自动生成测试数据
- **基本功能演示**: 数据加载、访问、采样、批处理
- **Hugging Face 集成**: 展示从 Hugging Face 加载数据集
- **数据变换**: 添加元数据的示例
- **自动清理**: 演示后自动删除临时文件

### 3. `huggingface_dataset_example.py` - Hugging Face 专门示例
专门展示 Hugging Face 数据集加载功能：

- **FLUX-Reason-6M 数据集**: 详细演示如何加载和使用
- **数据集探索**: 结构分析、字段查看
- **不同分割**: 尝试加载训练集和验证集
- **完整工作流**: 从加载到批处理的完整流程

## 运行示例

### 前置要求

```bash
# 安装必要的依赖
pip install pydantic datasets pandas pyarrow
```

### 运行命令

```bash
# 运行完整功能演示（需要本地 CSV 文件）
python dataset_example.py

# 运行简化演示（自包含，无需外部文件）
python simple_dataset_demo.py

# 运行 Hugging Face 专门示例
python huggingface_dataset_example.py
```

## 主要功能展示

### 1. 数据加载
```python
# 从本地文件加载
dataset = Dataset[Dict[str, Any]](name="my_dataset", data=[])
dataset.load_from(source="path/to/file.csv", format="csv")

# 从 Hugging Face 加载
dataset.load_from(source="LucasFang/FLUX-Reason-6M", split="train")
```

### 2. 采样器使用
```python
# 顺序采样器
seq_sampler = SequentialSampler(len(dataset.data))

# 随机采样器
rand_sampler = RandomSampler(len(dataset.data), seed=42)

# 批处理采样器
batch_sampler = BatchSampler(rand_sampler, batch_size=32, drop_last=False)
```

### 3. 批处理
```python
# 使用内置批处理
for batch in dataset.to_dataloader(batch_size=32, shuffle=True):
    # 处理批次数据
    pass

# 使用自定义采样器
for batch_indices in batch_sampler:
    batch = [dataset[i] for i in batch_indices]
    # 处理批次数据
```

### 采样器详解

`aworld.dataset.sampler` 提供了轻量级采样器以控制样本索引的产生与批次划分：

- **SequentialSampler(length: int)**: 顺序采样，产生 `[0, 1, ..., length-1]`。
  - **length**: 数据集长度，必须为非负整数。
  - 适用场景：严格顺序遍历、评测对比。

- **RandomSampler(length: int, seed: Optional[int] = None)**: 无放回随机采样，打乱 `[0, ..., length-1]`。
  - **length**: 数据集长度。
  - **seed**: 随机种子，可保证可复现的顺序。
  - 适用场景：随机训练/评测，需可复现时传入 `seed`。

- **BatchSampler(sampler: Sampler, batch_size: int, drop_last: bool)**: 将任意基础采样器封装成“批索引”采样器。
  - **sampler**: 基础索引采样器（如 `SequentialSampler` 或 `RandomSampler`）。
  - **batch_size**: 每批的索引数量，必须为正整数。
  - **drop_last**: 是否丢弃最后一个不完整批次。
  - 产出类型：`Iterator[List[int]]`，每次迭代返回一个索引列表。

示例：

```python
from aworld.dataset.sampler import SequentialSampler, RandomSampler, BatchSampler

num_items = len(dataset.data)

# 1) 顺序采样
seq_sampler = SequentialSampler(num_items)
print(list(seq_sampler)[:5])  # [0, 1, 2, 3, 4]

# 2) 随机采样（可复现）
rand_sampler = RandomSampler(num_items, seed=42)
print(list(rand_sampler)[:5])  # 例如 [8, 1, 5, 0, 3]

# 3) 批采样（基于随机采样器）
batch_sampler = BatchSampler(rand_sampler, batch_size=32, drop_last=False)
for idx_batch in batch_sampler:
    batch = [dataset[i] for i in idx_batch]
    # 处理批次数据
```

### 与 to_dataloader 的配合与互斥规则

`Dataset.to_dataloader` 提供了与采样器配合的两种方式：

- **显式传入 `sampler`**：
  - 当 `sampler` 被提供时，`shuffle` 参数被忽略，数据顺序完全由 `sampler` 决定。
  - 可同时指定 `batch_size`、`drop_last`。

- **显式传入 `batch_sampler`**：
  - 当提供 `batch_sampler` 时，以下参数必须保持默认或未提供：`batch_size`、`shuffle`、`sampler`、`drop_last`；否则会抛出错误。
  - 这种模式下，`to_dataloader` 直接按 `batch_sampler` 产生的索引批次取数据。

常见用法：

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

注意：

- `batch_size` 必须为正整数（当未提供 `batch_sampler` 时）。
- `drop_last=True` 会丢弃不足一满批的最后批次。
- 当数据项需要在取出时进行转换，可设置 `dataset.transform`，该转换同样在按索引取数/批处理时生效。

### 4. 数据变换
```python
def my_transform(item):
    # 自定义变换逻辑
    return transformed_item

dataset.transform = my_transform
```

## 支持的数据格式

- **本地文件**: CSV, JSON, TXT, Parquet
- **Hugging Face Hub**: 任何公开的数据集
- **内存数据**: 直接传入 Python 列表

## 注意事项

1. **Hugging Face 数据集**: 需要安装 `datasets` 库
2. **Parquet 文件**: 需要安装 `pandas` 或 `pyarrow`
3. **大数据集**: 使用 `limit` 参数限制加载数量
4. **内存使用**: 所有数据会加载到内存中

## 错误处理

所有示例都包含适当的错误处理：
- 文件不存在检查
- 依赖库缺失提示
- 数据集加载失败处理
- 网络连接问题处理

## 扩展功能

Dataset 类支持以下扩展：
- 自定义采样器
- 数据变换管道
- 元数据管理
- 数据集组合
- 缓存机制

这些示例展示了 `simplified_dataset.py` 的完整功能，可以作为学习和参考的起点。
