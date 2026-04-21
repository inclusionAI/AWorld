## Why

当前 `cron` 和 scheduler 只稳定支持“定时执行 + 终态通知”。对于某些扩展能力来说，这不够用，因为被调度任务有时需要在某次执行中产生用户可见事件，也有可能本次执行没有事件、应当静默结束。

框架层需要提供这种通用机制，但不能为了某个具体 query、具体数据源、或者某种领域能力去内建特化实现。

## What Changes

- 为 scheduler 通知链路增加 `user_visible` 语义，使被调度任务可以把某次执行标记为“用户可见事件”或“静默成功”。
- 为 scheduler 增加静默成功的处理路径：静默执行不推送用户消息，但当任务真的结束时仍可发布静默通知给渠道层做清理。
- 为 DingTalk cron fanout 增加静默清理支持，确保“静默结束”不会给用户发消息，但任务结束后仍能清理 `job_id -> session` 绑定。
- 为 CLI notification center 增加对静默通知的忽略逻辑，避免把内部清理事件展示成用户可见通知。

## Capabilities

### Added Capabilities
- `agent-runtime`: recurring cron jobs can decide per execution whether a successful result should become a user-visible notification or stay silent.
- `gateway-channels`: DingTalk cron fanout can suppress silent terminal notifications while still cleaning up routing bindings for finished jobs.

## Impact

- Affects `aworld/core/scheduler/*`, `aworld/core/task.py`.
- Affects `aworld_gateway/channels/dingding/cron_bindings.py` and `aworld-cli/src/aworld_cli/runtime/cron_notifications.py`.
- Preserves the existing DingTalk `job_id -> session` binding model and existing reminder-style cron behavior.
