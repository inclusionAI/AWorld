import os
import time
from aworld.logs.util import logger, trace_logger
from typing import Sequence
import aworld.trace as trace
from aworld.trace.base import Span
from aworld.trace.span_cosumer import register_span_consumer, SpanConsumer
from aworld.logs.util import logger, trace_logger

os.environ["MONITOR_SERVICE_NAME"] = "otlp_example"


@register_span_consumer({"test_param": "MockSpanConsumer111"})
class MockSpanConsumer(SpanConsumer):

    def __init__(self, test_param=None):
        self._test_param = test_param

    def consume(self, spans: Sequence[Span]) -> None:
        for span in spans:
            start_timestamp = span.start_time / 1e9
            end_timestamp = span.end_time / 1e9
            start_ms = int((span.start_time % 1e9) / 1e6)
            end_ms = int((span.end_time % 1e9) / 1e6)
            start_time = time.strftime(
                '%Y-%m-%d %H:%M:%S', time.localtime(start_timestamp)) + f'.{start_ms:03d}',
            end_time = time.strftime(
                '%Y-%m-%d %H:%M:%S', time.localtime(end_timestamp)) + f'.{end_ms:03d}',
            logger.info(
                f"[trace_span]={span.name}, trace_id={span.get_trace_id()}, span_id={span.get_span_id()}, start_time={start_time}, end_time={end_time}, duration_ms={(span.end_time - span.start_time)/1e6}, attributes={span.attributes}")


def main():
    with trace.span("hello") as span:
        span.set_attribute("parent_test_attr", "pppppp")
        logger.info("hello aworld")
        trace_logger.info("trace hello aworld")
