## 1. Implementation

- [x] 1.1 为 gateway server 模式增加 quiet boot 环境开关
- [x] 1.2 将 loader 的启动明细日志在 quiet boot 下从 `INFO/WARNING` 降到 `DEBUG`
- [x] 1.3 将 plugin manager 的扫描与 duplicate 启动明细在 quiet boot 下从 `INFO/WARNING` 降到 `DEBUG`
- [x] 1.4 补充 gateway CLI 与 boot logging 的回归测试

## 2. Validation

- [x] 2.1 运行 `pytest tests/gateway/test_gateway_status_command.py -q`
