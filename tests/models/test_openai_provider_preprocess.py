# coding: utf-8

import json

from aworld.models.openai_message_sanitizer import sanitize_openai_messages


def test_openai_provider_preprocess_drops_string_none_reasoning_details_and_extra_content():
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Let me check."}],
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                    "extra_content": "None",
                }
            ],
            "reasoning_details": "None",
        },
    ]

    processed = sanitize_openai_messages(messages)

    assert processed[2]["reasoning_details"] is None
    assert processed[2]["tool_calls"][0]["extra_content"] is None


def test_openai_provider_preprocess_keeps_valid_reasoning_details():
    reasoning_details = [{"type": "reasoning.text", "text": "step 1"}]
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Let me check."}],
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                    "extra_content": {"foo": "bar"},
                }
            ],
            "reasoning_details": reasoning_details,
        },
    ]

    processed = sanitize_openai_messages(messages)

    assert processed[0]["reasoning_details"] == reasoning_details
    assert processed[0]["tool_calls"][0]["extra_content"] == {"foo": "bar"}


def test_openai_provider_preprocess_drops_string_none_tool_calls():
    messages = [
        {
            "role": "assistant",
            "content": "Plain assistant reply.",
            "tool_calls": "None",
            "reasoning_details": "None",
        },
    ]

    processed = sanitize_openai_messages(messages)

    assert processed[0]["tool_calls"] is None
    assert processed[0]["reasoning_details"] is None
    assert processed[0]["content"] == "Plain assistant reply."


def test_openai_provider_preprocess_sanitizes_malformed_tool_call_arguments():
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Let me check."}],
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"cmd": "echo hi"'},
                }
            ],
        },
    ]

    processed = sanitize_openai_messages(messages)
    sanitized_arguments = json.loads(processed[0]["tool_calls"][0]["function"]["arguments"])

    assert sanitized_arguments["_aworld_replay"] == "compacted_tool_call_arguments"
    assert sanitized_arguments["tool_name"] == "bash"
    assert sanitized_arguments["sanitized_reason"] == "invalid_json_arguments"
