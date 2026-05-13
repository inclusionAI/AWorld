from aworld.core.llm_provider import LLMProviderBase


class DummyLLMProvider(LLMProviderBase):
    def _init_provider(self):
        return None

    def postprocess_response(self, response):
        return response

    def completion(self, messages, **kwargs):
        raise NotImplementedError


def test_accumulate_chunk_usage_coerces_string_and_none_values_recursively():
    provider = DummyLLMProvider(model_name="dummy", sync_enabled=False, async_enabled=False)
    usage = {
        "completion_tokens": 1,
        "prompt_tokens": 10,
        "total_tokens": 11,
        "prompt_tokens_details": {
            "cached_tokens": 4,
        },
    }

    provider._accumulate_chunk_usage(
        usage,
        {
            "completion_tokens": "2",
            "prompt_tokens": None,
            "total_tokens": "2",
            "prompt_tokens_details": {
                "cached_tokens": "3",
                "cache_creation_input_tokens": None,
                "cache_read_input_tokens": "1",
            },
        },
    )

    assert usage == {
        "completion_tokens": 3,
        "prompt_tokens": 10,
        "total_tokens": 13,
        "prompt_tokens_details": {
            "cached_tokens": 7,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 1,
        },
    }
