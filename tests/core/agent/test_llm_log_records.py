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


def test_log_llm_record_suppresses_stream_chunk_info_by_default(monkeypatch):
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

    assert fake_logger.entries == []


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

    assert len(fake_logger.entries) == 1
    _, debug_message, level = fake_logger.entries[0]
    assert "debug-only-reasoning" in debug_message
    assert level == "debug"


def test_stream_summary_is_one_bounded_content_free_info_record(monkeypatch):
    fake_logger = _FakeLogger()
    monkeypatch.setattr(util.llm_logger, "_logger", fake_logger)
    secret = "must-not-enter-stream-summary"

    util.log_llm_record(
        "STREAM_SUMMARY",
        "mock-model",
        {
            "terminal_status": "completed",
            "terminal_error": None,
            "reported": True,
            "chunk_count": 54_511,
            "content_chars_observed": 954,
            "reasoning_chars_observed": 594_307,
            "tool_call_chunks": 32,
            "tool_argument_chars_observed": 33_030,
            "first_chunk_latency_ms": 42_324,
            "duration_ms": 56_504,
            "opaque": secret,
        },
        {"request_id": "llm-req-stream"},
    )

    assert len(fake_logger.entries) == 1
    bound, message = fake_logger.entries[0]
    body = json.loads(message)
    assert body == {
        "schema_version": "aworld.llm-stream-summary-log.v1",
        "terminal_status": "completed",
        "terminal_error": None,
        "reported": True,
        "chunk_count": 54_511,
        "content_chars_observed": 954,
        "reasoning_chars_observed": 594_307,
        "tool_call_chunks": 32,
        "tool_argument_chars_observed": 33_030,
        "first_chunk_latency_ms": 42_324,
        "duration_ms": 56_504,
    }
    assert bound["direction"] == "STREAM_SUMMARY"
    assert secret not in message


def test_reasoning_selection_log_exports_only_bounded_receipt(monkeypatch):
    fake_logger = _FakeLogger()
    monkeypatch.setattr(util.llm_logger, "_logger", fake_logger)
    secret = "private-provider-payload"

    util.log_llm_record(
        "REASONING_SELECTION",
        "mock-model",
        {
            "phase": "execute",
            "source": "phase_policy",
            "reasoning_effort": "high",
            "thinking": True,
            "policy_id": "balanced/v1",
            "transport": "openai+chat_template/v1",
            "applied": True,
            "reason_code": "phase_policy_selected",
            "opaque": secret,
        },
    )

    assert len(fake_logger.entries) == 1
    bound, message = fake_logger.entries[0]
    assert json.loads(message) == {
        "schema_version": "aworld.reasoning-selection-log.v1",
        "status": "recorded",
        "phase": "execute",
        "source": "phase_policy",
        "reasoning_effort": "high",
        "thinking": True,
        "policy_id": "balanced/v1",
        "transport": "openai+chat_template/v1",
        "applied": True,
        "reason_code": "phase_policy_selected",
    }
    assert bound["direction"] == "REASONING_SELECTION"
    assert secret not in message


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
