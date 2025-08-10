# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
from collections import OrderedDict
from typing import List, Dict, Union, Any

from aworld.config import StorageConfig
from aworld.core.exceptions import AWorldRuntimeException
from aworld.core.storage.base import Storage, DataItem, DataBlock
from aworld.core.storage.condition import Condition, ConditionBuilder
from aworld.logs.util import logger
from aworld.utils.serialized_util import to_serializable


class InmemoryConfig(StorageConfig):
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


class InmemoryFilter:
    def __init__(self, condition: Condition) -> None:
        self.condition = condition

    def _get_field_value(self, data: DataItem, field: str) -> Any:
        """Get the field value from data."""
        if data.value:
            return getattr(data.value, field, None)
        else:
            raise AWorldRuntimeException(f"{data} no value to get.")

    def check_condition(self, data: DataItem, condition: Condition) -> bool:
        """Data match condition check."""
        if condition is None:
            return True
        if "field" in condition and "op" in condition:
            field_val = self._get_field_value(data, condition["field"])
            op = condition["op"]
            target_val = condition["value"]

            if op == "eq":
                return field_val == target_val
            if op == "ne":
                return field_val != target_val
            if op == "gt":
                return field_val > target_val
            if op == "gte":
                return field_val >= target_val
            if op == "lt":
                return field_val < target_val
            if op == "lte":
                return field_val <= target_val
            if op == "in":
                return field_val in target_val
            if op == "not_in":
                return field_val not in target_val
            if op == "like":
                return target_val in field_val
            if op == "not_like":
                return target_val not in field_val
            if op == "is_null":
                return field_val is None
            if op == "is_not_null":
                return field_val is not None
        elif "and_" in condition or "or_" in condition:
            if "and_" in condition:
                return all(self.check_condition(data, c) for c in condition["and_"])
            if "or_" in condition:
                return any(self.check_condition(data, c) for c in condition["or_"])

        return False

    def filter(self, data: List[DataItem], condition: Condition = None) -> List[DataItem]:
        """Filter data by condition.

        Args:
            data: List of data item to filter.
            condition: Data select condition.
        Returns:
            List[DataRow]: List of rows that match the condition.
        """
        if not condition:
            condition = self.condition

        if not condition:
            return data

        return [row for row in data if self.check_condition(row, condition)]


class InMemoryStorage(Storage[DataItem]):
    """In-memory storage."""

    def __init__(self, conf: InmemoryConfig):
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
        block_id = data.block_id if data.block_id else block_id
        if block_id not in self.blocks:
            await self.create_block(block_id)

        block_data = await self.get_data(block_id)
        if data in block_data:
            if overwrite:
                idx = block_data.index(data)
                block_data.insert(idx, data)
            else:
                logger.warning(f"Data {data.id} has exists.")
                return False
        else:
            self.datas[block_id].append(data)
        return True

    async def update_data(self, data: DataItem, block_id: str = None, exists: bool = False) -> bool:
        block_id = data.block_id if data.block_id else block_id
        block_data = await self.get_data(block_id)
        if data in block_data:
            idx = block_data.index(data)
            block_data.insert(idx, data)
        elif exists:
            logger.warning(f"Data {data.id} not exists to update.")
            return False
        return True

    async def delete_data(self, data: DataItem, block_id: str = None, exists: bool = False) -> bool:
        block_id = data.block_id if data.block_id else block_id
        block_data = await self.get_data(block_id)
        if data in block_data:
            block_data.remove(data)
        elif exists:
            logger.warning(f"Data {data.id} not exists to delete.")
            return False
        return True

    async def select_data(self, block_id: str = None, condition: Condition = None) -> List[DataItem]:
        if block_id:
            datas = self.datas.get(block_id, [])
        else:
            datas = []
            datas.extend(data for _, data in self.datas.items())

        if condition:
            datas = InmemoryFilter(condition).filter(datas)
        return datas

    async def get_data(self, block_id: str = None) -> List[DataItem]:
        return self.datas.get(block_id, [])

    async def close(self):
        self.blocks.clear()
        self.datas.clear()

    async def size(self, query_condition: Condition = None) -> int:
        return len(await self.select_data(query_condition))
