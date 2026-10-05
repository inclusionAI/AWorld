from types import SimpleNamespace

from aworld_cli.executors.local import _usage_log_summary


def test_usage_log_summary_keeps_only_fixed_numeric_fields() -> None:
    usage = {
        "prompt_tokens": 12,
        "completion_tokens": 7,
        "total_tokens": 19,
        "provider_metadata": {"secret": "must-not-reach-info-logs"},
    }

    assert _usage_log_summary(usage) == {
        "reported": True,
        "input_tokens": 12,
        "output_tokens": 7,
        "total_tokens": 19,
    }


def test_usage_log_summary_supports_standard_object_aliases() -> None:
    usage = SimpleNamespace(input_tokens=3, output_tokens=5, opaque="hidden")

    assert _usage_log_summary(usage) == {
        "reported": True,
        "input_tokens": 3,
        "output_tokens": 5,
        "total_tokens": None,
    }


def test_usage_log_summary_bounds_or_rejects_untrusted_values() -> None:
    usage = {
        "prompt_tokens": "do-not-log-or-coerce",
        "completion_tokens": float("inf"),
        "total_tokens": 10**100,
    }

    assert _usage_log_summary(usage) == {
        "reported": True,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": 2_147_483_647,
    }
