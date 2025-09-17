# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
from typing import Any, Literal, TypedDict, List, Union, Dict

from aworld.core.exceptions import AWorldRuntimeException
from aworld.core.storage.data import Data as DataItem


class BaseCondition(TypedDict):
    field: str
    value: Any
    op: Literal[
        'eq', 'ne', 'gt', 'gte', 'lt', 'lte',
        'in', 'not_in', 'like', 'not_like',
        'is_null', 'is_not_null'
    ]


class LogicalCondition(TypedDict):
    and_: List['Condition']
    or_: List['Condition']


Condition = Union[BaseCondition, LogicalCondition]


class ConditionBuilder:
    """Condition builder for storage of aworld.

    Examples:
    {
        "and": [
            {"field": "f1", "value": "v1", "op": "eq"},
            {"or": [{"field": "f2", "value": "v2", "op": "eq"}, {"field": "f3", "value": "v3", "op": "eq"}]}
        ]
    }
    """
    __metaclass__ = abc.ABCMeta

    def __init__(self, condition: Condition):
        self.condition = condition
        self.conditions: List[Dict[str, any]] = []
        self.logical_ops: List[str] = []

    @abc.abstractmethod
    def build(self) -> str:
        """Build a data selection condition string to query data from storage."""

    def eq(self, field: str, value: any) -> 'ConditionBuilder':
        self.conditions.append({"field": field, "value": value, "op": "eq"})
        return self

    def ne(self, field: str, value: any) -> 'ConditionBuilder':
        self.conditions.append({"field": field, "value": value, "op": "ne"})
        return self

    def gt(self, field: str, value: any) -> 'ConditionBuilder':
        self.conditions.append({"field": field, "value": value, "op": "gt"})
        return self

    def gte(self, field: str, value: any) -> 'ConditionBuilder':
        self.conditions.append({"field": field, "value": value, "op": "gte"})
        return self

    def lt(self, field: str, value: any) -> 'ConditionBuilder':
        self.conditions.append({"field": field, "value": value, "op": "lt"})
        return self

    def lte(self, field: str, value: any) -> 'ConditionBuilder':
        self.conditions.append({"field": field, "value": value, "op": "lte"})
        return self

    def in_(self, field: str, value: any) -> 'ConditionBuilder':
        self.conditions.append({"field": field, "value": value, "op": "in"})
        return self

    def not_in(self, field: str, value: any) -> 'ConditionBuilder':
        self.conditions.append({"field": field, "value": value, "op": "not_in"})
        return self

    def like(self, field: str, value: any) -> 'ConditionBuilder':
        self.conditions.append({"field": field, "value": value, "op": "like"})
        return self

    def not_like(self, field: str, value: any) -> 'ConditionBuilder':
        self.conditions.append(
            {"field": field, "value": value, "op": "not_like"})
        return self

    def is_null(self, field: str) -> 'ConditionBuilder':
        self.conditions.append({"field": field, "op": "is_null"})
        return self

    def is_not_null(self, field: str) -> 'ConditionBuilder':
        self.conditions.append({"field": field, "op": "is_not_null"})
        return self

    def and_(self) -> 'ConditionBuilder':
        self.logical_ops.append("and_")
        return self

    def or_(self) -> 'ConditionBuilder':
        self.logical_ops.append("or_")
        return self

    def nested(self, builder: 'ConditionBuilder') -> 'ConditionBuilder':
        self.conditions.append({"nested": builder.build()})
        return self


class ConditionFilter:
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
