from typing import Optional, Sequence
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter
from aworld.metrics.metric import Gauge, Histogram, MetricProvider, Counter, MetricExporter, UpDownCounter


class OpentelemetryMetricProvider(MetricProvider):
    """
    MetricProvider is a class for providing metrics.
    """

    def __init__(self, exporter: MetricExporter = None):
        """Initialize the MetricProvider.
        Args:
            exporter: The exporter of the metric.
        """
        super().__init__()
        if not exporter:
            exporter = ConsoleMetricExporter()
        self.exporter = exporter
        self._otel_provider = MeterProvider(
            metric_readers=[PeriodicExportingMetricReader(exporter=self.exporter, export_interval_millis=5000)])
        metrics.set_meter_provider(self._otel_provider)
        self._meter = self._otel_provider.get_meter("aworld")

    def create_counter(self,
                       name: str,
                       description: str,
                       unit: str,
                       labelnames: Optional[Sequence[str]] = None) -> Counter:
        """
        Create a counter.
        Args:
            name: The name of the counter.
            description: The description of the counter.
            unit: The unit of the counter.
        """
        return OpentelemetryCounter(name, description, unit, self)

    def create_un_down_counter(self,
                               name: str,
                               description: str,
                               unit: str,
                               labelnames: Optional[Sequence[str]] = None) -> UpDownCounter:
        """
        Create a un-down counter.
        Args:
            name: The name of the counter.
            description: The description of the counter.
            unit: The unit of the counter.
        """
        return OpentelemetryUpDownCounter(name, description, unit, self)

    def create_gauge(self,
                     name: str,
                     description: str,
                     unit: str,
                     labelnames: Optional[Sequence[str]] = None) -> Gauge:
        """
        Create a gauge.
        Args:
            name: The name of the gauge.
            description: The description of the gauge.
            unit: The unit of the gauge.
        """
        return OpentelemetryGauge(name, description, unit, self)

    def create_histogram(self,
                         name: str,
                         description: str,
                         unit: str,
                         buckets: Optional[Sequence[float]] = None,
                         labelnames: Optional[Sequence[str]] = None) -> Histogram:
        """
        Create a histogram.
        Args:
            name: The name of the histogram.
            description: The description of the histogram.
            unit: The unit of the histogram.
            buckets: The buckets of the histogram.
        """
        return OpentelemetryHistogram(name, description, unit, self, buckets)

    def shutdown(self):
        """
        Shutdown the metric provider.
        """
        pass


class OpentelemetryCounter(Counter):
    """
    OpentelemetryCounter is a subclass of Counter, representing a counter metric.
    A counter is a cumulative metric that represents a single numerical value that only ever goes up.
    """

    def __init__(self,
                 name: str,
                 description: str,
                 unit: str,
                 provider: OpentelemetryMetricProvider):
        """
        Initialize the Counter.
        Args:
            name: The name of the counter.
            description: The description of the counter.
            unit: The unit of the counter.
            provider: The provider of the counter.
        """
        super().__init__(name, description, unit, provider)
        self._counter = provider._meter.create_counter(name=name, description=description, unit=unit)

    def add(self, value: int, labels: dict = None) -> None:
        """
        Add a value to the counter.
        Args:
            value: The value to add to the counter.
            labels: The labels to associate with the value.
        """
        if labels is None:
            labels = {}
        self._counter.add(value, labels)


class OpentelemetryUpDownCounter(UpDownCounter):
    """
    OpentelemetryUpDownCounter is a subclass of UpDownCounter, representing an un-down counter metric.
    An un-down counter is a cumulative metric that represents a single numerical value that only ever goes up.
    """

    def __init__(self,
                 name: str,
                 description: str,
                 unit: str,
                 provider: OpentelemetryMetricProvider):
        """
        Initialize the UnDownCounter. 
        Args:
            name: The name of the counter.
            description: The description of the counter.
            unit: The unit of the counter.
            provider: The provider of the counter.
        """
        super().__init__(name, description, unit, provider)
        self._counter = provider._meter.create_up_down_counter(name=name, description=description, unit=unit)

    def inc(self, value: int, labels: dict = None) -> None:
        """
        Add a value to the counter.
        Args:
            value: The value to add to the counter.
            labels: The labels to associate with the value.
        """
        if labels is None:
            labels = {}
        self._counter.add(value, labels)

    def dec(self, value: int, labels: dict = None) -> None:
        """
        Subtract a value from the counter.
        Args:
            value: The value to subtract from the counter.
            labels: The labels to associate with the value.
        """
        if labels is None:
            labels = {}
        self._counter.add(-value, labels)


class OpentelemetryGauge(Gauge):
    """
    OpentelemetryGauge is a subclass of Gauge, representing a gauge metric.
    A gauge is a metric that represents a single numerical value that can arbitrarily go up and down.
    """

    def __init__(self,
                 name: str,
                 description: str,
                 unit: str,
                 provider: OpentelemetryMetricProvider):
        """
        Initialize the Gauge.
        Args:
            name: The name of the gauge.
            description: The description of the gauge.
            unit: The unit of the gauge.
            provider: The provider of the gauge.
        """
        super().__init__(name, description, unit, provider)
        self._gauge = provider._meter.create_gauge(name=name, description=description, unit=unit)

    def set(self, value: int, labels: dict = None) -> None:
        """
        Set the value of the gauge.
        Args:
            value: The value to set the gauge to.
            labels: The labels to associate with the value.
        """
        if labels is None:
            labels = {}
        self._gauge.set(value, labels)


class OpentelemetryHistogram(Histogram):
    """
    OpentelemetryHistogram is a subclass of Histogram, representing a histogram metric.
    A histogram is a metric that represents the distribution of a set of values.     
    """

    def __init__(self,
                 name: str,
                 description: str,
                 unit: str,
                 provider: OpentelemetryMetricProvider,
                 buckets: Sequence[float] = None):
        """
        Initialize the Histogram.
        Args:
            name: The name of the histogram.
            description: The description of the histogram.
            unit: The unit of the histogram.
            provider: The provider of the histogram.
            buckets: The buckets of the histogram.
        """
        super().__init__(name, description, unit, provider, buckets)
        self._histogram = provider._meter.create_histogram(name=name,
                                                           description=description,
                                                           unit=unit,
                                                           explicit_bucket_boundaries_advisory=buckets)

    def record(self, value: int, labels: dict = None) -> None:
        """
        Record a value in the histogram.
        Args:
            value: The value to record in the histogram.
            labels: The labels to associate with the value.
        """
        if labels is None:
            labels = {}
        self._histogram.record(value, labels)
