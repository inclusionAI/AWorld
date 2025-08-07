# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Any, Literal, TypedDict, List, Union


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
