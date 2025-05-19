
import logging
from typing import Collection, Literal, Callable
from aworld.utils.import_package import import_packages

import_packages(['fastapi', 'starlette'])  # noqa
import fastapi  # noqa


class _InstrumentedFastAPI(fastapi.FastAPI):
    """Instrumented FastAPI class."""
    _tracer_provider = None
    _excluded_urls = None
    _server_request_hook: Callable = None
    _client_request_hook: Callable = None
    _client_response_hook: Callable = None
    _instrumented_fastapi_apps = set()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        tracer = self._tracer_provider.get_tracer(
            "aworld.trace.instrumentation.fastapi")
            