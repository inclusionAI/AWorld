"""Failure-isolated observer helpers for run controllers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aworld.logs.util import logger


def safe_emit_progress(
    callback: Callable[[str, str], Any] | None,
    stage: str,
    message: str,
) -> None:
    """Notify an observer without allowing it to affect run semantics."""

    if callback is None:
        return
    try:
        callback(stage, message)
    except Exception as exc:
        logger.debug(
            f"self_evolve.progress_callback_failed stage={stage} error={exc}"
        )


__all__ = ["safe_emit_progress"]
