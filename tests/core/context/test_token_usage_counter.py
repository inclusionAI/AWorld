from aworld.utils.common import nest_dict_counter


def test_nest_dict_counter_coerces_string_and_none_values_in_nested_usage():
    merged = nest_dict_counter(
        {
            "prompt_tokens": 100,
            "completion_tokens": 5,
            "prompt_tokens_details": {
                "cached_tokens": 40,
                "cache_creation_input_tokens": 10,
            },
        },
        {
            "prompt_tokens": "20",
            "completion_tokens": None,
            "total_tokens": "20",
            "prompt_tokens_details": {
                "cached_tokens": "12",
                "cache_creation_input_tokens": None,
                "cache_read_input_tokens": "8",
            },
        },
    )

    assert merged == {
        "prompt_tokens": 120,
        "completion_tokens": 5,
        "total_tokens": 20,
        "prompt_tokens_details": {
            "cached_tokens": 52,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 8,
        },
    }
