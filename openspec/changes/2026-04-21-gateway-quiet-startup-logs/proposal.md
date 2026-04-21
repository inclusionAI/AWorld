# Proposal

## Why

`aworld-cli gateway server` 当前会把 agent loader / plugin manager 的大量启动明细直接打到控制台，掩盖了 DingTalk、cron scheduler、gateway HTTP server 这类更关键的运行信息。

## What Changes

- 为 gateway server 模式增加专用的 quiet boot 开关。
- 在 quiet boot 模式下，将 agent loader / plugin manager 的逐文件、逐目录、duplicate 明细日志降到 `DEBUG`。
- 保留 gateway、DingTalk、cron 相关的关键 `INFO` 级摘要和运行期日志。
- 为上述行为补充回归测试。
