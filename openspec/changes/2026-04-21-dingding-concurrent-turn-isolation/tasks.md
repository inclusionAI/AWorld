## 1. Implementation

- [x] 1.1 为 DingTalk connector 增加同会话重叠请求的独立 session 分配逻辑
- [x] 1.2 保持顺序消息继续复用已有 session
- [x] 1.3 为重叠请求隔离补充诊断日志
- [x] 1.4 补充同会话重叠请求的回归测试

## 2. Validation

- [x] 2.1 运行 `pytest tests/gateway/test_dingding_connector.py -q`
- [x] 2.2 运行 `pytest tests/gateway/test_dingding_connector.py tests/gateway/test_dingding_bridge.py tests/gateway/test_gateway_status_command.py -q`
