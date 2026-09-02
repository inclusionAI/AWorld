"""Validated baseline replay cache reuse policy."""

from __future__ import annotations

import json
from pathlib import Path

from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.replay import (
    _baseline_replay_is_reusable,
    _is_replayable_user_task_case,
    _load_variant_result_from_dir,
    _member_baseline_replay_dir,
)


def _reusable_baseline_case_count(
    *,
    dataset: SelfEvolveDataset,
    baseline_replay_dir: str | None,
    baseline_repetitions: int,
) -> int:
    """Count only validated cached controls when reserving replay work."""

    if baseline_replay_dir is None:
        return 0
    reusable = 0
    for case in dataset.cases:
        if not _is_replayable_user_task_case(case):
            continue
        try:
            stored_dir = _member_baseline_replay_dir(
                baseline_replay_dir,
                case.case_id,
            )
            if stored_dir is None:
                continue
            baseline = _load_variant_result_from_dir(
                Path(stored_dir),
                base_variant_id="baseline",
            )
        except (
            FileNotFoundError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            OSError,
        ):
            continue
        if _baseline_replay_is_reusable(
            baseline,
            requested_repetitions=baseline_repetitions,
        ):
            reusable += 1
    return reusable
