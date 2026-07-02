from aworld.models.utils import normalize_usage
from aworld.runners.event_runner import TaskEventRunner


def test_normalize_usage_maps_prompt_cache_fields():
    normalized = normalize_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cache_read_input_tokens": 80,
            "cache_creation_input_tokens": 40,
        }
    )

    assert normalized["prompt_tokens"] == 100
    assert normalized["completion_tokens"] == 20
    assert normalized["total_tokens"] == 120
    assert normalized["cache_hit_tokens"] == 80
    assert normalized["cache_write_tokens"] == 40
    assert "cache_read_input_tokens" not in normalized
    assert "cache_creation_input_tokens" not in normalized


def test_task_event_runner_finished_message_includes_cache_usage():
    message = TaskEventRunner._format_task_finished_message(
        task_id="task-1",
        is_sub_task=False,
        time_cost=1.25,
        token_usage={
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cache_hit_tokens": 80,
            "cache_write_tokens": 40,
        },
    )

    assert "main task task-1 finished" in message
    assert "cache_hit_tokens" in message
    assert "cache_write_tokens" in message
