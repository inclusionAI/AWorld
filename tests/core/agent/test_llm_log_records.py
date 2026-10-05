import json

from aworld.logs import util
from aworld.models.model_response import ModelResponse


class _FakeBoundLogger:
    def __init__(self, entries, payload):
        self._entries = entries
        self._payload = payload

    def info(self, message):
        self._entries.append((self._payload, message))

    def debug(self, message):
        self._entries.append((self._payload, message, "debug"))


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
    assert body["schema_version"] == "aworld.llm-stream-log.v1"
    assert body["content_chars"] == 2
    assert "raw_usage" not in body


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
    assert "prompt_cache_key_present=true" in bound["meta"]
    assert "stream_include_usage=True" in bound["meta"]


def test_log_llm_record_summarizes_stream_chunk_without_payload_text(monkeypatch):
    fake_logger = _FakeLogger()
    monkeypatch.setattr(util.llm_logger, "_logger", fake_logger)
    monkeypatch.delenv("AWORLD_LLM_LOG_RAW_CHUNKS", raising=False)
    secret = "private-reasoning-and-tool-argument"
    response = ModelResponse(
        id="resp-stream",
        model="mock-model",
        content="x" * 20_000,
        reasoning_content=secret * 1_000,
        message={"role": "assistant", "content": "x" * 20_000},
        tool_calls=[
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "terminal",
                    "arguments": secret * 1_000,
                },
            }
        ],
        usage={"completion_tokens": 7, "total_tokens": 7},
        finish_reason="tool_calls",
    )

    util.log_llm_record(
        "CHUNK",
        "mock-model",
        response,
        {"request_id": "llm-req-3", "stream_chunk_index": 9},
        "trace-3",
    )

    assert len(fake_logger.entries) == 1
    bound, message = fake_logger.entries[0]
    body = json.loads(message)
    assert body == {
        "schema_version": "aworld.llm-stream-log.v1",
        "content_chars": 20_000,
        "reasoning_chars": len(secret) * 1_000,
        "tool_call_count": 1,
        "tool_argument_chars": len(secret) * 1_000,
        "usage_reported": True,
        "finish_reason": "tool_calls",
        "provider_request_id_present": False,
    }
    assert "completion_tokens=7" in bound["meta"]
    assert secret not in message
    assert len(message) < 512


def test_raw_stream_chunk_logging_requires_explicit_debug_opt_in(monkeypatch):
    fake_logger = _FakeLogger()
    monkeypatch.setattr(util.llm_logger, "_logger", fake_logger)
    monkeypatch.setenv("AWORLD_LLM_LOG_RAW_CHUNKS", "true")
    response = ModelResponse(
        id="resp-stream",
        model="mock-model",
        reasoning_content="debug-only-reasoning",
    )

    util.log_llm_record("CHUNK", "mock-model", response)

    assert len(fake_logger.entries) == 2
    _, info_message = fake_logger.entries[0]
    _, debug_message, level = fake_logger.entries[1]
    assert "debug-only-reasoning" not in info_message
    assert "debug-only-reasoning" in debug_message
    assert level == "debug"


def test_input_output_and_params_info_logs_never_include_payload_text(monkeypatch):
    fake_logger = _FakeLogger()
    monkeypatch.setattr(util.llm_logger, "_logger", fake_logger)
    monkeypatch.delenv("AWORLD_LLM_LOG_RAW_PAYLOADS", raising=False)
    secret = "private-prompt-reasoning-or-tool-argument"

    util.log_llm_record(
        "INPUT",
        "mock-model",
        [{"role": "user", "content": secret}],
    )
    util.log_llm_record(
        "OUTPUT",
        "mock-model",
        ModelResponse(
            id="secret-output",
            model="mock-model",
            content=secret,
            reasoning_content=secret,
        ),
    )
    util.log_llm_record(
        "OPENAI_PARAMS",
        "mock-model",
        {
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "description": secret,
                        "parameters": {"secret": secret},
                    },
                }
            ],
            "extra_body": {"private": secret},
            "reasoning_effort": "max",
        },
    )

    assert len(fake_logger.entries) == 3
    for _, message in fake_logger.entries:
        assert secret not in message
        assert len(message) < 1024
