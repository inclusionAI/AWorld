# Proposal

## Why

当前 DingTalk channel 会把同一个 `conversationId` 长期绑定到单一 `session_id`。当用户在长任务尚未完成时继续发送下一条消息，两个请求会共享同一个 AWorld session、workspace 和 session 级历史，导致响应在用户侧表现为串行、串话或上下文互相污染。

## What Changes

- 为 DingTalk connector 增加“重叠请求隔离”策略。
- 顺序消息继续复用当前会话的 `session_id`，保持正常多轮上下文。
- 如果同一 DingTalk 会话里已有请求在执行，新消息改用新的独立 `session_id`，避免与正在运行的任务共享 session 级状态。
- 最新启动的消息成为该 DingTalk 会话后续顺序对话的当前 session。
- 为该行为补充 connector 级回归测试和诊断日志。
