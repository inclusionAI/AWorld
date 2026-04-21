## 1. Implementation

- [x] 1.1 为 `TaskResponse` 与 scheduler 通知链路增加通用 `user_visible` 语义
- [x] 1.2 在 scheduler 中实现“成功但静默”和“成功且用户可见”两种执行结果分支
- [x] 1.3 为 DingTalk cron fanout 和 CLI notification center 增加静默结束清理支持

## 2. Validation

- [x] 2.1 运行 `pytest tests/core/agent/test_aworld_prompt_policy.py tests/tools/test_cron_tool.py tests/core/scheduler/test_executor.py tests/core/scheduler/test_notifications.py tests/gateway/test_dingding_cron_bindings.py -q`
- [x] 2.2 运行 `pytest tests/gateway/test_dingding_connector.py tests/gateway/test_dingding_bridge.py tests/gateway/test_gateway_status_command.py -q`
