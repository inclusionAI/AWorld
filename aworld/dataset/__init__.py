"""Dataset public API with lazy imports.

Keeping this package initializer light lets persistence codecs be imported by
evaluation and CLI readers without initializing runtime state managers or
trajectory strategies.  The established public names remain available through
PEP 562 module attribute loading.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aworld.dataset.trajectory_dataset import (
        TrajectoryDataset,
        generate_trajectory,
        generate_trajectory_from_strategy,
    )
    from aworld.dataset.trajectory_strategy import (
        DefaultTrajectoryStrategy,
        FilteredTrajectoryStrategy,
        TrajectoryStrategy,
    )


_PUBLIC_MODULES = {
    "TrajectoryStrategy": "aworld.dataset.trajectory_strategy",
    "DefaultTrajectoryStrategy": "aworld.dataset.trajectory_strategy",
    "FilteredTrajectoryStrategy": "aworld.dataset.trajectory_strategy",
    "TrajectoryDataset": "aworld.dataset.trajectory_dataset",
    "generate_trajectory": "aworld.dataset.trajectory_dataset",
    "generate_trajectory_from_strategy": "aworld.dataset.trajectory_dataset",
}
_TASK_CONFIG_REBUILT = False


def __getattr__(name: str) -> Any:
    global _TASK_CONFIG_REBUILT
    module_name = _PUBLIC_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value

    # Preserve the old package contract: once a trajectory runtime type is
    # requested, resolve TaskConfig's forward references.
    if not _TASK_CONFIG_REBUILT:
        from aworld.config.conf import TaskConfig
        from aworld.dataset.trajectory_storage import TrajectoryStorage
        from aworld.dataset.trajectory_strategy import TrajectoryStrategy

        TaskConfig.model_rebuild(
            _types_namespace={
                "TrajectoryStrategy": TrajectoryStrategy,
                "TrajectoryStorage": TrajectoryStorage,
            }
        )
        _TASK_CONFIG_REBUILT = True
    return value

__all__ = [
    'TrajectoryStrategy',
    'DefaultTrajectoryStrategy',
    'FilteredTrajectoryStrategy',
    'TrajectoryDataset',
    'generate_trajectory',
    'generate_trajectory_from_strategy',
]
