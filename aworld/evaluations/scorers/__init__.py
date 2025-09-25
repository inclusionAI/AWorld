# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
import importlib
import pkgutil
from aworld.logs.util import logger

current_dir = os.path.dirname(__file__)


def _auto_discover_scorers():
    '''
    Auto-discover and import all scorer modules in the current directory.
    '''
    package_name = __name__
    for _, module_name, _ in pkgutil.iter_modules([current_dir]):
        try:
            importlib.import_module(f'.{module_name}', package=package_name)
        except Exception as e:
            logger.error(f"Failed to import scorer module {module_name}: {e}")


_auto_discover_scorers()


from aworld.evaluations.scorers.scorer_registry import (
    ScorerRegistry,
    global_scorer_registry,
    register_scorer_class,
    unregister_scorer_class,
    get_scorer_instances_for_metric,
    get_scorer_instances_for_criterias,
    scorer_register
)

from aworld.evaluations.scorers.metrics import MetricNames

__all__ = [
    'ScorerRegistry',
    'global_scorer_registry',
    'register_scorer_class',
    'unregister_scorer_class',
    'get_scorer_instances_for_metric',
    'get_scorer_instances_for_criterias',
    'scorer_register',
    'MetricNames'
]
