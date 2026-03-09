# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from collections import OrderedDict
from typing import List, Dict, Literal, Union

from aworld.config import StorageConfig
from aworld.core.storage.base import Storage, DataItem, DataBlock
from aworld.core.storage.condition import Condition, ConditionBuilder, ConditionFilter
from aworld.logs.util import logger
from aworld.utils.serialized_util import to_serializable


class InmemoryConfig(StorageConfig):
    name: str = "inmemory"
    # Maximum number of blocks stored globally; oldest block is evicted when exceeded.
    max_capacity: int = 10000
    # Maximum number of data items per block; 0 means unlimited.
    max_items_per_block: int = 1000
    # Eviction policy applied within each block: "lru" or "fifo".
    eviction_policy: Literal["lru", "fifo"] = "fifo"


class InmemoryConditionBuilder(ConditionBuilder):
    def build(self) -> str:
        conditions = self.conditions  # all conditions（including nested）
        operators = self.logical_ops

        # Validate condition and operator counts (n conditions need n-1 operators)
        if len(operators) != len(conditions) - 1:
            raise ValueError("Mismatch between condition and operator counts")

        # Use stack to handle operator precedence (simplified version supporting and/or)
        stack: List[Union[Dict[str, any], str]] = []

        for i, item in enumerate(conditions):
            if i == 0:
                # First element goes directly to stack (condition or nested)
                stack.append(item)
                continue

            # Pop stack top as left operand
            left = stack.pop()
            op = operators[i - 1]  # Current operator (and/or)
            right = item  # Right operand (current condition)

            # Build logical expression: {op: [left, right]}
            expr = {op: [left, right]}
            # Push result back to stack for further operations
            stack.append(expr)

        # Process nested conditions (recursive unfolding)
        def process_nested(cond: any) -> any:
            if isinstance(cond, dict):
                if "nested" in cond:
                    # Recursively process sub-conditions
                    return process_nested(cond["nested"])
                # Recursively process child elements
                return {k: process_nested(v) for k, v in cond.items()}
            elif isinstance(cond, list):
                return [process_nested(item) for item in cond]
            return cond

        # Final result: only one element left in stack, return after processing nested
        result = process_nested(stack[0]) if stack else None
        return to_serializable(result)


class InmemoryStorage(Storage[DataItem]):
    """In-memory storage.

    Each block's data items are stored in an OrderedDict keyed by item id,
    enabling O(1) lookup and efficient FIFO/LRU eviction when the per-block
    capacity limit is reached.

    Eviction policies (controlled by ``InmemoryConfig.eviction_policy``):
    - ``"fifo"``: evicts the oldest inserted item first.
    - ``"lru"`` (default): evicts the least-recently-written item first;
      the order is updated on every write (create / update).
    """

    def __init__(self, conf: InmemoryConfig = None):
        if not conf:
            conf = InmemoryConfig()
        super().__init__(conf)

        self.blocks: Dict[str, DataBlock] = OrderedDict()
        # Each block maps item-id -> DataItem via an OrderedDict for O(1) access
        # and ordered eviction.
        self.datas: Dict[str, OrderedDict] = OrderedDict()
        self.max_capacity = conf.max_capacity
        self.max_items_per_block = conf.max_items_per_block
        self.eviction_policy = conf.eviction_policy

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evict_oldest_block(self) -> None:
        """Remove the globally oldest block together with its data."""
        oldest_block_id, _ = next(iter(self.blocks.items()))
        self.blocks.pop(oldest_block_id)
        self.datas.pop(oldest_block_id, None)
        logger.warning(
            f"Global block capacity {self.max_capacity} reached, evicted block '{oldest_block_id}'"
        )

    def _evict_block_item(self, block_id: str, block_data: OrderedDict) -> None:
        """Remove one item from *block_data* according to the eviction policy."""
        evicted_id, _ = next(iter(block_data.items()))
        block_data.pop(evicted_id)
        logger.debug(
            f"Block '{block_id}' max_items_per_block {self.max_items_per_block} reached, "
            f"evicted item '{evicted_id}' ({self.eviction_policy})"
        )

    def _touch(self, block_data: OrderedDict, data_id: str) -> None:
        """Move *data_id* to the end of *block_data* for LRU tracking."""
        if self.eviction_policy == "lru":
            block_data.move_to_end(data_id)

    # ------------------------------------------------------------------
    # Block operations
    # ------------------------------------------------------------------

    def backend(self):
        return self

    async def create_block(self, block_id: str, overwrite: bool = True) -> bool:
        if block_id in self.blocks:
            if not overwrite:
                logger.warning(f"{block_id} has exists.")
                return False

        self.blocks[block_id] = DataBlock(id=block_id)
        return True

    async def delete_block(self, block_id: str, exists: bool = False) -> bool:
        if block_id in self.blocks:
            self.blocks.pop(block_id)
            self.datas.pop(block_id, None)
        else:
            logger.warning(f"{block_id} not exists.")
            return False
        return True

    async def get_block(self, block_id: str) -> DataBlock:
        return self.blocks.get(block_id)

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    def _get_block_dict(self, block_id: str) -> OrderedDict:
        """Return the internal OrderedDict for *block_id*, creating it if absent.

        This is the internal accessor used by write operations so they can
        mutate the dict in-place (insert, overwrite, evict).  External callers
        should use ``get_data_items`` which returns a plain list.
        """
        if block_id not in self.datas:
            self.datas[block_id] = OrderedDict()
        return self.datas[block_id]

    async def create_data(self, data: DataItem, block_id: str = None, overwrite: bool = True) -> bool:
        block_id = str(data.block_id if hasattr(data, "block_id") and data.block_id else block_id)
        if block_id not in self.blocks:
            if len(self.blocks) >= self.max_capacity:
                self._evict_oldest_block()
            await self.create_block(block_id)

        block_data = self._get_block_dict(block_id)
        data_id = data.id if hasattr(data, "id") else str(data)

        if data_id in block_data:
            if overwrite:
                block_data[data_id] = data
                self._touch(block_data, data_id)
            else:
                logger.warning(f"Data {data_id} has exists.")
                return False
        else:
            if self.max_items_per_block > 0 and len(block_data) >= self.max_items_per_block:
                self._evict_block_item(block_id, block_data)
            block_data[data_id] = data
        return True

    async def update_data(self, data: DataItem, block_id: str = None, exists: bool = False) -> bool:
        block_id = str(data.block_id if hasattr(data, "block_id") and data.block_id else block_id)
        block_data = self._get_block_dict(block_id)
        data_id = data.id if hasattr(data, "id") else str(data)

        if data_id in block_data:
            block_data[data_id] = data
            self._touch(block_data, data_id)
        elif exists:
            logger.warning(f"Data {data_id} not exists to update.")
            return False
        return True

    async def delete_data(self,
                          data_id: str = None,
                          data: DataItem = None,
                          block_id: str = None,
                          exists: bool = False) -> bool:
        block_id = str(block_id)
        block_data = self._get_block_dict(block_id)

        # Resolve the actual key: prefer explicit data_id, fall back to data.id
        key = None
        if data_id and data_id in block_data:
            key = data_id
        elif data is not None:
            candidate = data.id if hasattr(data, "id") else str(data)
            if candidate in block_data:
                key = candidate

        if key is not None:
            block_data.pop(key)
        elif exists:
            logger.warning(f"Data {data_id} not exists to delete.")
            return False
        return True

    async def select_data(self, condition: Condition = None) -> List[DataItem]:
        datas = []
        for block_data in self.datas.values():
            datas.extend(block_data.values())

        if condition:
            datas = ConditionFilter(condition).filter(datas)
        return datas

    async def get_data_items(self, block_id: str = None) -> List[DataItem]:
        block_id = str(block_id)
        return list(self._get_block_dict(block_id).values())

    async def delete_all(self):
        self.blocks.clear()
        self.datas.clear()

    async def size(self, query_condition: Condition = None) -> int:
        return len(await self.select_data(query_condition))
