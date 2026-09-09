# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import json
import traceback
from enum import Enum

import numpy as np

from aworld.logs.util import logger


class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def to_serializable(obj, _memo=None):
    # Immutable scalar objects (especially small integers and interned strings)
    # may legitimately be shared in many fields.  Treating their object IDs as
    # cycle markers corrupts provider usage truth, e.g. the second integer 5
    # becomes the string "5".  Only compound/object values need cycle tracking.
    if isinstance(obj, Enum):
        return obj.value
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if _memo is None:
        _memo = set()
    obj_id = id(obj)
    if obj_id in _memo:
        return str(obj)
    _memo.add(obj_id)
    try:
        if isinstance(obj, dict):
            return {k: to_serializable(v, _memo) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple, set)):
            return [to_serializable(i, _memo) for i in obj]
        elif isinstance(obj, asyncio.Task):
            # asyncio.Task is not JSON-serializable; often appears when serializing context/trajectory
            name = obj.get_name() if hasattr(obj, "get_name") else ""
            logger.debug("to_serializable: asyncio.Task replaced with string (name=%s)", name)
            return repr(obj)
        elif asyncio.iscoroutine(obj):
            logger.debug("to_serializable: coroutine replaced with string: %s", obj)
            return repr(obj)
        elif hasattr(obj, "to_dict"):
            return obj.to_dict()
        elif hasattr(obj, "model_dump"):
            return obj.model_dump()
        elif hasattr(obj, "dict"):
            return obj.dict()
        elif hasattr(obj, "__dataclass_fields__"):
            return {field.name: to_serializable(getattr(obj, field.name), _memo)
                    for field in obj.__dataclass_fields__.values()}
        elif hasattr(obj, "__dict__"):
            return {k: to_serializable(v, _memo) for k, v in obj.__dict__.items()
                    if not k.startswith('_') and not callable(v)}
        else:
            try:
                json.dumps(obj)
                return obj
            except TypeError as e:
                logger.error(
                    "Failed to serialize object: type=%s obj_repr=%s error=%s\n%s",
                    type(obj).__name__,
                    repr(obj)[:200],
                    e,
                    traceback.format_exc(),
                )
                raise RuntimeError(f"{e}")
    finally:
        # `_memo` is the active recursion stack, not a global seen set. Shared
        # provider evidence may legitimately appear in more than one redacted
        # projection and must not be stringified as though it were a cycle.
        _memo.discard(obj_id)
