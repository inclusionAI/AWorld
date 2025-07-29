import requests
from urllib.parse import urljoin
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.sdk._logs import LoggerProvider as SDKLoggerProvider
from opentelemetry.sdk._logs._internal import SynchronousMultiLogRecordProcessor
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

from ..log import LoggerProvider
class OTLPLoggerProvider(LoggerProvider):

    def __init__(self,
                 backend: str = None,
                 base_url: str = None,
                 write_token: str = None,
                 **kwargs
    ) -> None:
        self._logger_provider = _build_otlp_privider(backend=backend,
                                               base_url=base_url,
                                               write_token=write_token)



def _build_otlp_privider(backend: str = None, base_url: str = None, write_token: str = None):
    """Build the otlp provider.
    Args:
        backends: The backends to use.
        write_token: The write token to use.
        **kwargs: Additional keyword arguments to pass to the provider.
    Returns:
        The otlp provider.
    """
    backend = backend or "logfire"
    if backend == "logfire":
        log_exporter = _configure_logfire_exporter(write_token=write_token, base_url=base_url)
    else:
        raise ValueError(f"Unknown backend: {backend}")

    otlp_log_processor = BatchLogRecordProcessor(log_exporter)
    multi_log_processor = SynchronousMultiLogRecordProcessor()
    multi_log_processor.add_log_record_processor(otlp_log_processor)
    logger_provider = SDKLoggerProvider()
    logger_provider.add_log_record_processor(multi_log_processor)
    return logger_provider


def _configure_logfire_exporter(write_token: str, base_url: str = None) -> OTLPLogExporter:
    """Configure the Logfire exporter.
    Args:
        write_token: The write token to use.
        base_url: The base URL to use.
        **kwargs: Additional keyword arguments to pass to the exporter.
    """
    base_url = base_url or "https://logfire-us.pydantic.dev"
    headers = {'User-Agent': f'logfire/3.14.0', 'Authorization': write_token}
    session = requests.Session()
    session.headers.update(headers)
    return OTLPLogExporter(
                endpoint=urljoin(base_url, '/v1/logs'),
                session=session,
                compression=Compression.Gzip,
    )
