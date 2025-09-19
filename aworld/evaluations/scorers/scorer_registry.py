from typing import Dict, List, Set, Optional, Type, Any
from aworld.evaluations.base import Scorer, EvalCriteria
from aworld.logs.util import logger


class ScorerRegistry:
    '''
    Scorer registry for managing scorers and their associated metric names.
    '''

    _instance: Optional['ScorerRegistry'] = None
    _lock = False

    def __new__(cls):
        if cls._instance is None:
            cls._lock = True
            try:
                if cls._instance is None:
                    cls._instance = super(ScorerRegistry, cls).__new__(cls)
                    cls._instance._initialize()
            finally:
                cls._lock = False
        return cls._instance

    def _initialize(self):
        self._metric_to_scorers: Dict[str, List[Type[Scorer]]] = {}
        self._scorer_to_metrics: Dict[int, Set[str]] = {}
        self._default_scorer_params: Dict[int, Dict[str, Any]] = {}

    def register_scorer_class(self, scorer_class: Type[Scorer], metric_names: List[str], **default_params) -> None:
        '''
        Register a scorer class with one or more metric names.

        Args:
            scorer_class: The scorer class to register
            metric_names: List of metric names associated with this scorer
            **default_params: Default parameters to use when creating scorer instances
        '''
        scorer_id = id(scorer_class)

        if scorer_id in self._scorer_to_metrics:
            self.unregister_scorer_class(scorer_class)

        self._scorer_to_metrics[scorer_id] = set(metric_names)
        self._default_scorer_params[scorer_id] = default_params

        for metric_name in metric_names:
            if metric_name not in self._metric_to_scorers:
                self._metric_to_scorers[metric_name] = []

            if scorer_class not in self._metric_to_scorers[metric_name]:
                self._metric_to_scorers[metric_name].append(scorer_class)

    def unregister_scorer_class(self, scorer_class: Type[Scorer]) -> None:
        '''
        Unregister a scorer from the registry.

        Args:
            scorer: The scorer instance to unregister
        '''

        scorer_id = id(scorer_class)

        if scorer_id not in self._scorer_to_metrics:
            return

        for metric_name in self._scorer_to_metrics[scorer_id]:
            if metric_name in self._metric_to_scorers:
                self._metric_to_scorers[metric_name] = [s for s in self._metric_to_scorers[metric_name] if s != scorer_class]
            if not self._metric_to_scorers[metric_name]:
                del self._metric_to_scorers[metric_name]

        if scorer_id in self._default_scorer_params:
            del self._default_scorer_params[scorer_id]
        del self._scorer_to_metrics[scorer_id]

    def get_scorer_classes_for_metric(self, metric_name: str) -> List[Type[Scorer]]:
        '''
        Get all scorers classes registered for a specific metric name.

        Args:
            metric_name: The metric name to get scorers for

        Returns:
            List of scorer classes associated with the metric name
        '''
        return self._metric_to_scorers.get(metric_name, [])

    def create_scorer_instance(self, scorer_class: Type[Scorer], criteria: EvalCriteria = None) -> Scorer:
        '''
        Create a scorer instance using parameters from EvalCriteria and defaults.

        Args:
            scorer_class: The scorer class to instantiate
            criteria: EvalCriteria object containing scorer parameters

        Returns:
            Scorer instance
        '''
        scorer_id = id(scorer_class)
        params = self._default_scorer_params.get(scorer_id, {}).copy()
        if criteria and criteria.scorer_params:
            params.update(criteria.scorer_params)
        scorer = scorer_class(**params)
        scorer.add_eval_criteria(criteria)
        return scorer

    def get_scorer_instances_for_metric(self, criteria: EvalCriteria = None) -> List[Scorer]:
        '''
        Get scorer instances for a metric name, using parameters from EvalCriteria and defaults.

        Args:
            criteria: EvalCriteria object containing scorer parameters

        Returns:
            List of scorer instances associated with the metric name
        '''
        instances = []
        for scorer_class in self.get_scorer_classes_for_metric(criteria.metric_name):
            instances.append(self.create_scorer_instance(scorer_class, criteria))
        return instances

    def get_scorer_instances_for_criterias(self, criterias: List[EvalCriteria]) -> List[Scorer]:
        '''
        Get a mapping of scorer instances to their associated EvalCriteria based on metric names.

        Args:
            criterias: List of EvalCriteria objects

        Returns:
            Dictionary mapping scorer instances to list of EvalCriteria they should handle
        '''
        scorer_instances: Dict[Type[Scorer], Scorer] = {}

        for criteria in criterias:
            scorer_classes = self.get_scorer_classes_for_metric(criteria.metric_name)
            if not scorer_classes:
                logger.error(f'No scorer class found for metric {criteria.metric_name}')
                raise ValueError(f'No scorer class found for metric {criteria.metric_name}')
            scorer_type: Type[Scorer] = None
            if criteria.scorer_class:
                for scorer_class in scorer_classes:
                    if scorer_class.__name__ == criteria.scorer_class:
                        scorer_type = scorer_class
                        break
                if not scorer_type:
                    logger.error(f'No scorer class found for {criteria.scorer_class} and metric {criteria.metric_name}')
                    raise ValueError(f'No scorer class found for {criteria.scorer_class} and metric {criteria.metric_name}')
            else:
                scorer_type = scorer_classes[0]

            if scorer_type not in scorer_instances:
                scorer = self.create_scorer_instance(scorer_type, criteria)
                scorer_instances[scorer_type] = scorer
            else:
                scorer_instances[scorer_type].add_eval_criteria(criteria)

        return list(scorer_instances.values())


global_scorer_registry = ScorerRegistry()


def register_scorer_class(scorer_class: Type[Scorer], metric_names: List[str], **default_params) -> None:
    '''
    Register a scorer class with the global registry.
    '''
    global_scorer_registry.register_scorer_class(scorer_class, metric_names, **default_params)


def unregister_scorer_class(scorer_class: Type[Scorer]) -> None:
    '''
    Unregister a scorer class from the global registry.
    '''
    global_scorer_registry.unregister_scorer_class(scorer_class)


def get_scorer_instances_for_metric(criteria: EvalCriteria = None) -> List[Scorer]:
    '''
    Get scorer instances for a metric name from the global registry.
    '''
    return global_scorer_registry.get_scorer_instances_for_metric(criteria)


def get_scorer_instances_for_criterias(criterias: List[EvalCriteria]) -> List[Scorer]:
    '''
    Get scorer to criteria mapping from the global registry.
    '''
    return global_scorer_registry.get_scorer_instances_for_criterias(criterias)


def scorer_register(*metric_names: str, **default_params):
    '''
    A decorator to register scorer classes automatically.

    Args:
        *metric_names: Metric names associated with the scorer
        **default_params: Default parameters to use when creating scorer instances
    '''
    def decorator(scorer_class: Type[Scorer]):
        if not issubclass(scorer_class, Scorer):
            raise TypeError(f"{scorer_class.__name__} must be a subclass of Scorer")
        global_scorer_registry.register_scorer_class(scorer_class, list(metric_names), **default_params)
        return scorer_class

    return decorator