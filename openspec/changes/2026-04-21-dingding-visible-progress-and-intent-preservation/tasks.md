## 1. Implementation

- [x] 1.1 为 DingTalk 复杂请求增加即时处理中确认消息
- [x] 1.2 为 DingTalk 下游输入增加原始约束保留执行要求
- [x] 1.3 补充 connector 级回归测试
- [x] 1.4 为 AI Card 创建、投递、流式刷新和结束收口补充诊断日志
- [x] 1.5 将 AI Card 不可用时的降级策略调整为“复杂请求立即 ack、非复杂任务型请求延时 ack、超短对话型请求不 ack”
- [x] 1.6 为 AI Card 降级与流式诊断补充 connector 级回归测试

## 2. Validation

- [x] 2.1 运行 `pytest tests/gateway/test_dingding_connector.py -q`
- [x] 2.2 运行 `pytest tests/gateway/test_dingding_connector.py tests/gateway/test_dingding_bridge.py tests/gateway/test_gateway_status_command.py -q`
