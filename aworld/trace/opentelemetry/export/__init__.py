# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import typing
from os import linesep
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult, SpanExporter
from aworld.logs.util import logger


class FileSpanExporter(SpanExporter):
    """Implementation of :class:`SpanExporter` that prints spans to the
    console.

    This class can be used for diagnostic purposes. It prints the exported
    spans to the console STDOUT.
    """

    def __init__(
        self,
        file_path: str = None,
        formatter: typing.Callable[
            [ReadableSpan], str
        ] = lambda span: span.to_json() + linesep,
    ):
        self.formatter = formatter
        self.file_path = file_path

    def export(self, spans: typing.Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            with open(self.file_path, 'a') as f:
                for span in spans:
                    f.write(self.formatter(span))

            return SpanExportResult.SUCCESS
        except Exception as e:
            logger.error(e)
            return SpanExportResult.FAILURE

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class NoOpSpanExporter(SpanExporter):
    """Implementation of :class:`SpanExporter` that does not export spans."""

    def export(
        self, spans: typing.Sequence[ReadableSpan]
    ) -> SpanExportResult:
        return SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
