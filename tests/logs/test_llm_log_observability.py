from aworld.logs.util import log_llm_record
from aworld.models.model_response import ModelResponse


class _FakeBoundLogger:
    def __init__(self, sink):
        self._sink = sink

    def info(self, message):
        self._sink["message"] = message


class _FakeLogger:
    def __init__(self):
        self.bound = {}
        self.message = None

    def bind(self, **kwargs):
        self.bound = kwargs
        return _FakeBoundLogger({"message": None, **self.__dict__})


def test_log_llm_record_includes_request_linked_cache_observability(monkeypatch):
    fake_logger = _FakeLogger()

    class _Recorder:
        def __init__(self, logger):
            self._logger = logger

    monkeypatch.setattr("aworld.logs.util.llm_logger", _Recorder(fake_logger))

    response = ModelResponse(
        id="resp_123",
        model="gpt-4.1",
        content="done",
        usage={"prompt_tokens": 100, "completion_tokens": 25, "total_tokens": 125},
        raw_usage={
            "prompt_tokens": 100,
            "completion_tokens": 25,
            "total_tokens": 125,
            "cache_hit_tokens": 80,
            "cache_write_tokens": 20,
        },
        provider_request_id="req_provider_123",
    )

    log_llm_record(
        "OUTPUT",
        "gpt-4.1",
        response,
        {"request_id": "llm_req_123"},
        trace_id="trace-1",
    )

    assert fake_logger.bound["direction"] == "OUTPUT"
    assert "request_id=llm_req_123" in fake_logger.bound["meta"]
    assert "provider_request_id=req_provider_123" in fake_logger.bound["meta"]
    assert "cache_hit_tokens=80" in fake_logger.bound["meta"]
    assert "cache_write_tokens=20" in fake_logger.bound["meta"]
