"""requests instrumentor reads REQUESTS_EXCLUDED_URLS, not FLASK_EXCLUDED_URLS.

Flask reads FLASK_EXCLUDED_URLS and FastAPI reads FASTAPI_EXCLUDED_URLS via
get_excluded_urls(name). The requests client instrumentor was copied from
Flask and still passed "FLASK", so REQUESTS_EXCLUDED_URLS was silently ignored
and the Flask list leaked into client HTTP calls.
"""
from __future__ import annotations

from aworld.trace.instrumentation.requests import (
    RequestsInstrumentor,
    _InstrumentedSession,
)

REQUESTS_LIST = "https://a.internal,https://b.metrics"
FLASK_LIST = "https://f.example"


def _instrument_with_env(monkeypatch, requests_env, flask_env):
    if requests_env is None:
        monkeypatch.delenv("REQUESTS_EXCLUDED_URLS", raising=False)
    else:
        monkeypatch.setenv("REQUESTS_EXCLUDED_URLS", requests_env)
    if flask_env is None:
        monkeypatch.delenv("FLASK_EXCLUDED_URLS", raising=False)
    else:
        monkeypatch.setenv("FLASK_EXCLUDED_URLS", flask_env)

    instrumentor = RequestsInstrumentor()
    instrumentor._instrument(tracer_provider=None)
    try:
        return list(_InstrumentedSession._excluded_urls or [])
    finally:
        instrumentor._uninstrument()


def test_requests_excluded_urls_comes_from_requests_env(monkeypatch) -> None:
    excluded = _instrument_with_env(
        monkeypatch, requests_env=REQUESTS_LIST, flask_env=None,
    )

    assert excluded == ["https://a.internal", "https://b.metrics"]


def test_flask_env_does_not_leak_into_requests(monkeypatch) -> None:
    excluded = _instrument_with_env(
        monkeypatch, requests_env=None, flask_env=FLASK_LIST,
    )

    assert excluded == []
