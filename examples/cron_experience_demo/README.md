# Cron Experience Demo

## What this demonstrates

- one-time reminders
- recurring scheduled tasks
- task listing and cleanup
- asynchronous notifications with unread inbox flow
- isolated cron state for repeatable demos

## Claude Scheduled Tasks Mental Model

- Claude "Set a one-time reminder" -> AWorld one-time cron task
- Claude "Manage scheduled tasks" -> AWorld `/cron list`, `/cron remove`, `/cron disable`, `/cron enable`
- Claude recurring scheduled checks -> AWorld recurring cron or interval tasks
- Claude background scheduled execution -> AWorld scheduler + notification center

## Manual Walkthrough

From the repository root, use a disposable working directory so the demo does not mix scheduler state with the example source files:

```bash
cd examples/cron_experience_demo
mkdir -p .manual_runtime
cd .manual_runtime
aworld-cli
```

Inside the CLI, try these steps:

1. Create a one-time reminder:

```text
/cron add 2分钟后提醒我检查 cron demo 输出目录
```

2. Create a recurring heartbeat task:

```text
/cron add 每分钟向 outputs/heartbeat.log 追加一行带 UTC 时间戳的 heartbeat
```

3. List scheduled jobs:

```text
/cron list
```

4. Wait for the reminder to fire. The main chat stays unblocked and the bottom
   status bar should show unread cron notifications.

5. Read unread reminder notifications:

```text
/cron inbox
```

6. Remove the demo jobs you created:

```text
/cron remove all
```

Manual-run artifacts land under:

- `.manual_runtime/.aworld/cron.json`
- `.manual_runtime/outputs/heartbeat.log`

## Automated Demo

Run the isolated demo from the repository root:

```bash
python -m examples.cron_experience_demo.run_auto_demo
```

The auto demo uses:

- `examples/cron_experience_demo/.demo_runtime/.aworld/cron.json`
- `examples/cron_experience_demo/.demo_runtime/outputs/heartbeat.log`
- `examples/cron_experience_demo/.demo_runtime/outputs/reminder.txt`

It prints:

- current jobs
- terminal notifications with reminder detail when available
- latest heartbeat file state

## Environment Note

Both paths use the real cron scheduler, cron store, and notification center.

- The manual walkthrough also uses the normal agent execution path, so it depends on a locally registered `Aworld` agent. If `Aworld` is missing, manual jobs can still be scheduled and listed, but executions may finish with `error`.
- The automated demo uses a built-in deterministic demo executor so it can always show successful scheduling, notifications, and output-file updates even when no local `Aworld` agent is configured.
- Reminder notifications are consumed through `/cron inbox`; they do not interrupt or block the main conversation loop.
