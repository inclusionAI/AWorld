import json

from aworld.logs import util
from aworld.models.model_response import ModelResponse


class _FakeBoundLogger:
    def __init__(self, entries, payload):
        self._entries = entries
        self._payload = payload

    def info(self, message):
        self._entries.append((self._payload, message))


class _FakeLogger:
    def __init__(self):
        self.entries = []

    def bind(self, **kwargs):
        return _FakeBoundLogger(self.entries, kwargs)


def test_log_llm_record_adds_cache_observability_to_meta(monkeypatch):
    fake_logger = _FakeLogger()
    monkeypatch.setattr(util.llm_logger, "_logger", fake_logger)

    response = ModelResponse(
        id="resp-1",
        model="mock-model",
        content="ok",
        usage={
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
            "prompt_tokens_details": {"cached_tokens": 5},
        },
        provider_request_id="req-123",
    )

    util.log_llm_record(
        "OUTPUT",
        "mock-model",
        response,
        {"task_id": "task-1", "request_id": "llm-req-1"},
        "trace-1",
    )

    bound, message = fake_logger.entries[0]
    body = json.loads(message)

    assert "cache_hit_tokens=5" in bound["meta"]
    assert "provider_request_id=req-123" in bound["meta"]
    assert body["raw_usage"]["prompt_tokens_details"]["cached_tokens"] == 5


def test_log_llm_record_adds_prompt_cache_request_metadata(monkeypatch):
    fake_logger = _FakeLogger()
    monkeypatch.setattr(util.llm_logger, "_logger", fake_logger)

    util.log_llm_record(
        "OPENAI_PARAMS",
        "mock-model",
        {
            "prompt_cache_key": "cache-key-1",
            "stream": True,
            "stream_options": {"include_usage": True},
        },
        {"request_id": "llm-req-2"},
        "trace-2",
    )

    bound, _ = fake_logger.entries[0]
    assert "prompt_cache_key=cache-key-1" in bound["meta"]
    assert "stream_include_usage=True" in bound["meta"]
