import re
import time
import asyncio
from typing import Callable
from functools import wraps
from aworld.core.metrics.metric import get_metric_provider, MetricType, BaseMetric
from aworld.core.metrics.template import MetricTemplate, MetricTemplates

_GLOBAL_METIRCS={}

class MetricContext:

    @staticmethod
    def get_or_create_metric(template: MetricTemplate):
        if template.name in _GLOBAL_METIRCS:
            return _GLOBAL_METIRCS[template.name]
        
        metric = None
        if template.type == MetricType.COUNTER:
            metric = get_metric_provider().create_counter(template.name, template.description, template.unit, template.labels)
        elif template.type == MetricType.UPDOWNCOUNTER:
            metric = get_metric_provider().create_updowncounter(template.name, template.description, template.unit, template.labels)
        elif template.type == MetricType.GAUGE:
            metric = get_metric_provider().create_gauge(template.name, template.description, template.unit, template.labels)
        elif template.type == MetricType.HISTOGRAM:
            metric = get_metric_provider().create_histogram(template.name, template.description, template.unit, template.buckets, template.labels)
        
        _GLOBAL_METIRCS[template.name] = metric
        return metric

    @classmethod
    def _validate_type(cls, metric: BaseMetric, type: str):
        if type != metric._type:
            raise ValueError(f"metric type {metric._type} is not {type}")

    @classmethod
    def count(cls, template: MetricTemplate, value: int, labels: dict = None):
        """
        Increment a counter metric.
        """
        metric = cls.get_or_create_metric(template)
        cls._validate_type(metric, MetricType.COUNTER)
        metric.add(value, labels)

    @classmethod
    def inc(cls, template: MetricTemplate, value: int, labels: dict = None):
        """
        Increment a updowncounter metric.
        """
        metric = cls.get_or_create_metric(template)
        cls._validate_type(metric, MetricType.UPDOWNCOUNTER)
        metric.inc(value, labels)
    
    @classmethod
    def dec(cls, template: MetricTemplate, value: int, labels: dict = None):
        """
        Decrement a updowncounter metric.
        """
        metric = cls.get_or_create_metric(template)
        cls._validate_type(metric, MetricType.UPDOWNCOUNTER)
        metric.dec(value, labels)

    @classmethod
    def gauge_set(cls, template: MetricTemplate, value: int, labels: dict = None):
        """
        Set a value to a gauge metric.
        """
        metric = cls.get_or_create_metric(template)
        cls._validate_type(metric, MetricType.GAUGE)
        metric.set(value, labels)

    @classmethod
    def histogram_record(cls, template: MetricTemplate, value: int, labels: dict = None):
        """
        Set a value to a histogram metric.
        """
        metric = cls.get_or_create_metric(template)
        cls._validate_type(metric, MetricType.HISTOGRAM)
        metric.record(value, labels)
    

class ApiMetricTracker:
    """
    Decorator to track API metrics.
    """
    def __init__(self, api_name: str = None, func: Callable = None):
        self.start_time = None
        self.status = "success"
        self.func = func
        self.api_name = api_name
        if self.api_name is None and self.func is not None:
            self.api_name = self.func.__name__

    def _new_tracker(self, func: Callable):
        return self.__class__(func=func)

    def __enter__(self):
        self.start_time = time.time() * 1000

    def __exit__(self, exc_type, value, traceback):
        if exc_type is None:
            self.status = "success"
        else:
            self.status = "failure"
        self._record_metrics(self.api_name, self.start_time, self.status)

    def __call__(self, func: Callable = None) -> Callable:
        if func is None:
            return self
        return self.decorator(func)

    def _record_metrics(self, api_name: str, start_time: float, status: str) -> None:
        """
        Record metrics for the API.
        """
        elapsed_time = time.time() * 1000 - start_time
        MetricContext.count(MetricTemplates.REQUEST_COUNT, 1, 
                      labels={"method": api_name, "status": status})
        MetricContext.histogram_record(MetricTemplates.REQUEST_LATENCY, elapsed_time,
                                 labels={"method": api_name, "status": status})

    def decorator(self, func):
        """
        Decorator to track API metrics.
        """
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            with self._new_tracker(func):
                return await func(*args, **kwargs) 
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            with self._new_tracker(func):
                return func(*args, **kwargs)
    
        return async_wrapper if asyncio.iscoroutinefunction(func) else wrapper