# coding: utf-8
# Copyright (c) inclusionAI.
from collections import OrderedDict
from typing import Dict, List, Type, Any, Union

from aworld.core.factory import Factory
from aworld.evaluations.base import Scorer, EvalCriteria
from aworld.logs.util import logger


class ScorerFactory(Factory):
    """Scorer factory for managing scorers and their associated metric names."""

    def __init__(self, type_name: str = None):
        super().__init__(type_name)
        self._metric_to_scorers: Dict[str, Type[Scorer]] = {}
        self._default_scorer_params: Dict[int, Dict[str, Any]] = {}

    def __call__(self, name: str = None, criterias: Union[EvalCriteria, List[EvalCriteria]] = None, *args, **kwargs):
        if not name and not criterias:
            raise ValueError('Either name or criterias must be provided')

        if not criterias:
            scorer_cls = self._metric_to_scorers.get(name)
            criterias = EvalCriteria(metric_name=name, scorer_class=scorer_cls, scorer_params=kwargs)

        return self.get_scorer_instances_for_criterias(criterias)

    def register(self, name: str, desc: str = '', scorer_cls: Type[Scorer] = None, **kwargs):
        """Register a scorer class with one or more metric names.

        Args:
            name: Metric names associated with this scorer
            scorer_cls: The scorer class to register
            desc: Metric description.
            **kwargs: Default parameters to use when creating scorer instances
        """
        scorer_id = id(scorer_cls)

        self._default_scorer_params[scorer_id] = kwargs

        if name not in self._metric_to_scorers:
            self._metric_to_scorers[name] = scorer_cls
        else:
            raise ValueError(f'Scorer class {scorer_cls.__name__} already registered for metric {name}')

    def unregister(self, name: str):
        scorer_cls = self._metric_to_scorers.pop(name, None)

        scorer_id = id(scorer_cls)
        if scorer_id in self._metric_to_scorers:
            del self._default_scorer_params[scorer_id]

    def create_scorer_instance(self, scorer_class: Type[Scorer], criteria: EvalCriteria = None) -> Scorer:
        """Create a scorer instance using parameters from EvalCriteria and defaults.

        Args:
            scorer_class: The scorer class to instantiate
            criteria: EvalCriteria object containing scorer parameters

        Returns:
            Scorer instance
        """
        scorer_id = id(scorer_class)
        params = self._default_scorer_params.get(scorer_id, {}).copy()
        if criteria and criteria.scorer_params:
            params.update(criteria.scorer_params)
        scorer = scorer_class(**params)
        scorer.add_eval_criteria(criteria)
        return scorer

    def get_scorer_instances_for_criterias(self, criterias: Union[EvalCriteria, List[EvalCriteria]]) -> List[Scorer]:
        """Get list of scorer instances to their associated EvalCriteria based on metric names.

        Args:
            criterias: List of EvalCriteria objects

        Returns:
            Dictionary mapping scorer instances to list of EvalCriteria they should handle
        """
        scorer_instances: Dict[Type[Scorer], Scorer] = OrderedDict()
        if isinstance(criterias, EvalCriteria):
            criterias = [criterias]

        for criteria in criterias:
            scorer_class = self._metric_to_scorers.get(criteria.metric_name)
            if not scorer_class:
                logger.error(f'No scorer class found for metric {criteria.metric_name}')
                raise ValueError(f'No scorer class found for metric {criteria.metric_name}')

            if criteria.scorer_class and  scorer_class.__name__ != criteria.scorer_class:
                raise ValueError(f"registered scorer class {scorer_class.__name__} does not match criteria {criteria.scorer_class}")

            if scorer_class not in scorer_instances:
                scorer = self.create_scorer_instance(scorer_class, criteria)
                scorer_instances[scorer_class] = scorer
            else:
                scorer_instances[scorer_class].add_eval_criteria(criteria)

        return list(scorer_instances.values())


scorer_factory = ScorerFactory('scorer_factory')


def scorer_register(*metric_names: str, **kwargs):
    """A decorator to register scorer classes automatically.

    Args:
        *metric_names: Metric names associated with the scorer
        **kwargs: Default parameters to use when creating scorer instances
    """

    def decorator(scorer_class: Type[Scorer]):
        if not issubclass(scorer_class, Scorer):
            raise TypeError(f"{scorer_class.__name__} must be a subclass of Scorer")
        for metrics_name in metric_names:
            scorer_factory.register(name=metrics_name, scorer_cls=scorer_class, **kwargs)
        return scorer_class

    return decorator
