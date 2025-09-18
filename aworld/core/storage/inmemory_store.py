# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from collections import OrderedDict
from typing import List, Dict, Union

from aworld.config import StorageConfig
from aworld.core.storage.base import Storage, DataItem, DataBlock
from aworld.core.storage.condition import Condition, ConditionBuilder, ConditionFilter
from aworld.logs.util import logger
from aworld.utils.serialized_util import to_serializable


class InmemoryConfig(StorageConfig):
    name: str = "inmemory"
    max_capacity: int = 10000


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
    """In-memory storage."""

    def __init__(self, conf: InmemoryConfig = None):
        if not conf:
            conf = InmemoryConfig()
        super().__init__(conf)

        self.blocks: Dict[str, DataBlock] = OrderedDict()
        self.datas: Dict[str, List[DataItem]] = OrderedDict()
        self.max_capacity = conf.max_capacity

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
        else:
            logger.warning(f"{block_id} not exists.")
            return False
        return True

    async def get_block(self, block_id: str) -> DataBlock:
        return self.blocks.get(block_id)

    async def create_data(self, data: DataItem, block_id: str = None, overwrite: bool = True) -> bool:
        block_id = str(data.block_id if hasattr(data, "block_id") and data.block_id else block_id)
        if block_id not in self.blocks:
            await self.create_block(block_id)

        block_data = await self.get_data_items(block_id)
        if data in block_data:
            if overwrite:
                idx = block_data.index(data)
                block_data.__setitem__(idx, data)
            else:
                logger.warning(f"Data {data.id} has exists.")
                return False
        else:
            self.datas[block_id].append(data)
        return True

    async def update_data(self, data: DataItem, block_id: str = None, exists: bool = False) -> bool:
        block_id = str(data.block_id if hasattr(data, "block_id") and data.block_id else block_id)
        block_data = await self.get_data_items(block_id)
        if data in block_data:
            idx = block_data.index(data)
            block_data.__setitem__(idx, data)
        elif exists:
            logger.warning(f"Data {data.id} not exists to update.")
            return False
        return True

    async def delete_data(self,
                          data_id: str = None,
                          data: DataItem = None,
                          block_id: str = None,
                          exists: bool = False) -> bool:
        block_id = str(block_id)
        block_data = await self.get_data_items(block_id)
        del_data = None
        for data_item in block_data:
            if hasattr(data_item, "id"):
                if data_item.id == data_id:
                    del_data = data_item
                    break
            elif data == data_item:
                del_data = data_item
                break

        if del_data:
            block_data.remove(del_data)
        elif exists:
            logger.warning(f"Data {data_id} not exists to delete.")
            return False
        return True

    async def select_data(self, condition: Condition = None) -> List[DataItem]:
        datas = []
        for _, data in self.datas.items():
            datas.extend(data)

        if condition:
            datas = ConditionFilter(condition).filter(datas)
        return datas

    async def get_data_items(self, block_id: str = None) -> List[DataItem]:
        block_id = str(block_id)
        if block_id not in self.datas:
            self.datas[block_id] = []
        return self.datas.get(block_id, [])

    async def delete_all(self):
        self.blocks.clear()
        self.datas.clear()

    async def size(self, query_condition: Condition = None) -> int:
        return len(await self.select_data(query_condition))
