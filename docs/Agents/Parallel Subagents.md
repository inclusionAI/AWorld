# Parallel Subagents

Parallel subagents let one coordinating agent launch multiple independent sub-tasks at the same time instead of waiting for each one sequentially.

## What It Does

Use parallel subagents when you have work that can be split cleanly, such as:

- comparing multiple datasets
- reviewing several code areas at once
- running validation steps in parallel
- gathering independent research results before synthesis

This reduces total wall-clock time from roughly the sum of all sub-tasks to the duration of the slowest sub-task plus coordination overhead.

## Basic Pattern

```python
spawn_subagent(
    action="spawn_parallel",
    tasks=[
        {"name": "analyzer1", "directive": "Analyze dataset A"},
        {"name": "analyzer2", "directive": "Analyze dataset B"},
        {"name": "reporter", "directive": "Summarize the findings"},
    ],
    max_concurrent=3,
    aggregate=True,
)
```

## Key Inputs

- `tasks`: the list of subagent jobs to run
- `max_concurrent`: the concurrency cap
- `aggregate`: whether to return a combined summary
- `fail_fast`: whether the coordinator should stop on the first failure

Each task should provide a clear `name` and `directive`. Optional fields such as model overrides or tool restrictions can be added when the runtime supports them.

## When To Use It

Use parallel subagents when:

- the subtasks are independent
- each subtask has a clear owner and objective
- you want faster turnaround more than strict sequential reasoning

Avoid it when:

- later steps depend tightly on earlier results
- subtasks need to share mutable state continuously
- cost or rate limits require serialized execution

## Notes

- Keep directives narrow and explicit.
- Set `max_concurrent` to match your runtime and provider limits.
- Use `aggregate=True` when the parent agent needs a user-facing summary instead of raw sub-task outputs.
