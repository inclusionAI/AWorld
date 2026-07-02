from aworld.runners.event_runner import TaskEventRunner


def test_task_finished_message_includes_prompt_cache_summary():
    message = TaskEventRunner._format_task_finished_message(
        task_id="task-1",
        is_sub_task=False,
        time_cost=12.5,
        token_usage={
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "total_tokens": 1050,
            "prompt_tokens_details": {
                "cached_tokens": 300,
                "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 100,
            },
        },
    )

    assert "main task task-1 finished, time cost: 12.5s" in message
    assert "token cost: {'prompt_tokens': 1000, 'completion_tokens': 50, 'total_tokens': 1050" in message
    assert (
        "prompt cache: {'cache_read_tokens': 100, 'cache_write_tokens': 200, "
        "'cache_related_tokens': 300, 'cache_read_ratio': 0.1, "
        "'cache_write_ratio': 0.2, 'cache_related_ratio': 0.3}"
    ) in message
