from __future__ import annotations

import importlib
from typing import Callable

from aworld.self_evolve.optimizers.base import OptimizerRequest, OptimizerResult


class DSPyGEPAOptimizer:
    def __init__(self, *, import_module: Callable[[str], object] = importlib.import_module) -> None:
        self.import_module = import_module

    async def propose(self, request: OptimizerRequest) -> OptimizerResult:
        try:
            self.import_module("dspy")
        except ImportError as exc:
            raise ImportError(
                "DSPy optimizer 'gepa' requires optional dependency 'dspy'"
            ) from exc
        raise NotImplementedError("DSPy GEPA optimizer adapter is not implemented in phase 1a")
