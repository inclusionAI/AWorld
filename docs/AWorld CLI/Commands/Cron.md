# Cron

## What It Does

The cron command manages scheduled tasks directly from an interactive CLI session. It bypasses the model and calls the scheduler tool directly, so status and task-management actions are deterministic.

## Commands

```text
/cron
/cron add <natural-language request>
/cron list
/cron show <job_id>
/cron inbox [job_id]
/cron run <job_id>
/cron enable <job_id|all>
/cron disable <job_id|all>
/cron remove <job_id|all>
/cron status
```

Supported aliases:

- `/cron rm <job_id>`
- `/cron delete <job_id>`

## Typical Workflow

1. Create a reminder with `/cron add remind me to run tests at 6pm`.
2. Inspect the queue with `/cron list`.
3. Open a single job with `/cron show <job_id>`.
4. Read unread reminder notifications with `/cron inbox`.
5. Disable, re-enable, run, or remove the job as needed.

## Notes And Limits

- `/cron` with no arguments is the same as `/cron list`.
- In the interactive terminal, `/cron show <job_id>` can switch into live follow mode when the job is currently running.
- `next_run=None` is expected for completed one-shot reminders or disabled jobs.
- `enabled=False` with `last_status=ok` means the job finished or was disabled cleanly, not that it failed.
