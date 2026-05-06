# Parallel Tasks

## What It Does

AWorld CLI supports background task execution so you can submit a long-running task, keep working in the same session, and check progress later.

The primary commands are `/dispatch` for submission and `/tasks` for monitoring.

## Commands

```text
/dispatch <task description>
/dispatch
/tasks list
/tasks status <task_id>
/tasks follow <task_id>
/tasks cancel <task_id>
```

## Typical Workflow

1. Submit work with `/dispatch Run the benchmark and summarize failures`.
2. Continue using the CLI while the task runs in the background.
3. Inspect the queue with `/tasks list`.
4. Check one task with `/tasks status <task_id>`.
5. Stream live output with `/tasks follow <task_id>`.
6. Cancel the task if needed with `/tasks cancel <task_id>`.

## Notes And Limits

- `/dispatch` without arguments prompts for the task text interactively.
- Background tasks use the default `Aworld` agent unless your runtime provides a different dispatch setup.
- `/tasks list` shows the output filename when one exists, so you can tail the log directly from the shell if needed.
