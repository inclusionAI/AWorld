# coding: utf-8
# Copyright (c) inclusionAI.
import abc
from typing import TypeVar, Generic

from aworld.config import EvaluationConfig
from aworld.core.storage.base import Storage
from aworld.core.storage.inmemory_store import InmemoryStorage

INPUT = TypeVar('INPUT')
OUTPUT = TypeVar('OUTPUT')


class EvalRecorder(abc.ABC, Generic[INPUT, OUTPUT]):
    def __init__(self, storage: Storage = None, eval_config: EvaluationConfig = None):
        self.storage = storage or InmemoryStorage()
        self._config = eval_config

    @property
    def eval_config(self):
        return self._config

    @eval_config.setter
    def eval_config(self, eval_config: EvaluationConfig):
        self._config = eval_config

    @abc.abstractmethod
    async def record(self, eval_input: INPUT, **kwargs) -> OUTPUT:
        """Record the evaluation result.

        Args:
            eval_input: The evaluation result.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def get_by_key(self, key: str, **kwargs) -> OUTPUT:
        """Get the evaluation related output by the key.

        Args:
            key: The key of clause. `str` is limited now, need improve!

        Returns:
            OUTPUT: The evaluation result.
        """
        raise NotImplementedError
