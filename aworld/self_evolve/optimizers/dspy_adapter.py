from __future__ import annotations

import hashlib
import importlib
from typing import Callable

from aworld.self_evolve.optimizers.base import OptimizerRequest, OptimizerResult
from aworld.self_evolve.candidate_package import (
    candidate_content_semantic_fingerprint,
)
from aworld.self_evolve.types import CandidateVariant, OptimizerLineage


class DSPyGEPAOptimizer:
    optimizer_name = "dspy-gepa"
    optimizer_version = "0"

    def __init__(self, *, import_module: Callable[[str], object] = importlib.import_module) -> None:
        self.import_module = import_module

    async def propose(self, request: OptimizerRequest) -> OptimizerResult:
        dspy = _import_dspy(self.import_module, optimizer_label="gepa")
        return _delegate_dspy_optimizer(
            dspy,
            request,
            optimizer_name=self.optimizer_name,
            optimizer_version=self.optimizer_version,
            callable_names=("GEPA", "gepa"),
        )


class DSPyMIPROOptimizer:
    optimizer_name = "dspy-mipro"
    optimizer_version = "0"

    def __init__(self, *, import_module: Callable[[str], object] = importlib.import_module) -> None:
        self.import_module = import_module

    async def propose(self, request: OptimizerRequest) -> OptimizerResult:
        dspy = _import_dspy(self.import_module, optimizer_label="mipro")
        return _delegate_dspy_optimizer(
            dspy,
            request,
            optimizer_name=self.optimizer_name,
            optimizer_version=self.optimizer_version,
            callable_names=("MIPRO", "mipro"),
        )


def _import_dspy(import_module: Callable[[str], object], *, optimizer_label: str) -> object:
    try:
        return import_module("dspy")
    except ImportError as exc:
        raise ImportError(
            f"DSPy optimizer '{optimizer_label}' requires optional dependency 'dspy'"
        ) from exc


def _delegate_dspy_optimizer(
    dspy_module: object,
    request: OptimizerRequest,
    *,
    optimizer_name: str,
    optimizer_version: str,
    callable_names: tuple[str, ...],
) -> OptimizerResult:
    optimizer_callable = None
    for name in callable_names:
        optimizer_callable = getattr(dspy_module, name, None)
        if callable(optimizer_callable):
            break
    if optimizer_callable is None:
        raise ImportError(
            f"DSPy module does not expose any of: {', '.join(callable_names)}"
        )

    output = optimizer_callable(request)
    if not isinstance(output, dict):
        raise ValueError("DSPy optimizer output must be a mapping with content")
    content = output.get("content")
    if not isinstance(content, str) or not content:
        raise ValueError("DSPy optimizer output must include non-empty content")
    rationale = output.get("rationale", "")
    if not isinstance(rationale, str):
        rationale = ""

    candidate_id = f"{optimizer_name}-{abs(hash((request.target.target_type, request.target.target_id, content))) % 10**12:012d}"
    candidate = CandidateVariant(
        candidate_id=candidate_id,
        target=request.target,
        content=content,
        rationale=rationale,
        target_fingerprint=request.target_fingerprint,
    )
    return OptimizerResult(
        candidates=(candidate,),
        lineage=(
            OptimizerLineage(
                candidate_id=candidate_id,
                optimizer_name=optimizer_name,
                optimizer_version=optimizer_version,
                trainable_case_ids=tuple(case.case_id for case in request.trainable_cases),
                content_fingerprint=_content_fingerprint(content),
                semantic_fingerprint=candidate_content_semantic_fingerprint(
                    content
                ),
                rationale=rationale,
            ),
        ),
    )


def _content_fingerprint(content: str) -> str:
    normalized = "\n".join(line.rstrip() for line in content.strip().splitlines())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
