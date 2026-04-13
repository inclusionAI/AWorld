# AWorld Hooks 示例脚本

本目录包含实用的 Shell hook 示例，展示如何使用 Hooks V2 系统实现各种安全策略、审计日志、输入输出修改等场景。

## 目录结构

```
hooks/
├── README.md                    # 本文档
├── audit_logger.sh              # 审计日志示例
├── path_sanitizer.sh            # 路径参数清理
├── output_filter.sh             # 输出内容过滤
├── dangerous_command_blocker.sh # 危险命令拦截
├── rate_limiter.sh              # 速率限制
└── session_notification.sh      # 会话通知
```

## 使用方法

### 1. 配置文件方式（推荐）

在项目根目录创建 `.aworld/hooks.yaml`：

```yaml
version: "v2"
hooks:
  - name: "audit-logger"
    hook_point: "after_tool_call"
    enabled: true
    command: "./docs/examples/hooks/audit_logger.sh"
    timeout: 5000  # 5秒超时

  - name: "path-sanitizer"
    hook_point: "before_tool_call"
    enabled: true
    command: "./docs/examples/hooks/path_sanitizer.sh"
```

### 2. 环境变量

所有 hook 脚本都可以访问以下环境变量：

- `AWORLD_SESSION_ID`: 会话 ID
- `AWORLD_TASK_ID`: 任务 ID
- `AWORLD_CWD`: 当前工作目录
- `AWORLD_HOOK_POINT`: Hook 触发点名称
- `AWORLD_MESSAGE_JSON`: Message 对象的 JSON 序列化
- `AWORLD_CONTEXT_JSON`: Context 对象的 JSON 序列化

### 3. Hook 输出格式

所有 hook 脚本必须输出 JSON 格式的 HookJSONOutput：

```json
{
  "continue": true,
  "stop_reason": null,
  "system_message": null,
  "additional_context": null,
  "permission_decision": null,
  "permission_decision_reason": null,
  "updated_input": null,
  "updated_output": null,
  "async": false,
  "async_task_id": null,
  "async_timeout": null
}
```

**最小有效输出：**
```json
{"continue": true}
```

**阻止后续执行：**
```json
{
  "continue": false,
  "stop_reason": "Reason for stopping",
  "permission_decision": "deny"
}
```

**修改输入（before_tool_call）：**
```json
{
  "continue": true,
  "updated_input": [{"tool_name": "terminal", "args": {"command": "safe command"}}]
}
```

**修改输出（after_tool_call）：**
```json
{
  "continue": true,
  "updated_output": {
    "observation": {"content": "filtered content"}
  }
}
```

## 示例场景

### 审计日志 (audit_logger.sh)

记录所有工具调用到审计日志文件：

- Hook 点：`after_tool_call`
- 功能：记录工具名称、会话 ID、时间戳、执行结果
- 输出：添加 `audit_logged: true` 到工具返回的 `info` 字段

### 路径清理 (path_sanitizer.sh)

清理文件路径参数，防止路径遍历攻击：

- Hook 点：`before_tool_call`
- 功能：检测 `../` 等危险路径模式，替换为安全路径
- 输出：修改后的 `updated_input`

### 输出过滤 (output_filter.sh)

过滤工具输出中的敏感信息：

- Hook 点：`after_tool_call`
- 功能：检测密码、API key 等敏感信息，替换为 `[REDACTED]`
- 输出：修改后的 `updated_output`

### 危险命令拦截 (dangerous_command_blocker.sh)

阻止执行危险的系统命令：

- Hook 点：`before_tool_call`
- 功能：检测 `rm -rf`、`dd if=/dev/zero` 等危险命令
- 输出：`continue: false` 阻止执行

### 速率限制 (rate_limiter.sh)

限制工具调用频率，防止滥用：

- Hook 点：`before_tool_call`
- 功能：基于会话 ID 跟踪调用次数，超过阈值则拒绝
- 输出：达到限制时返回 `continue: false`

### 会话通知 (session_notification.sh)

在会话开始/结束时发送通知：

- Hook 点：`session_started`, `session_finished`
- 功能：调用 Webhook 或发送 Slack 消息
- 输出：`continue: true`（不影响执行）

## 安全注意事项

1. **WorkspaceTrust**: 确保 `.aworld/hooks.yaml` 位于受信任的工作区
2. **权限控制**: Hook 脚本应设置正确的文件权限（`chmod 755`）
3. **超时设置**: 避免 hook 脚本执行时间过长，建议设置 `timeout`
4. **Fail-open 策略**: Hook 执行失败不会阻塞主流程，但会记录警告日志
5. **环境变量**: 不要在 hook 脚本中硬编码敏感信息，使用环境变量

## 调试技巧

### 启用调试日志

```bash
export AWORLD_LOG_LEVEL=DEBUG
aworld-cli
```

### 测试 Hook 脚本

```bash
# 设置测试环境变量
export AWORLD_SESSION_ID="test-session-123"
export AWORLD_TASK_ID="test-task-456"
export AWORLD_HOOK_POINT="before_tool_call"
export AWORLD_MESSAGE_JSON='{"category":"tool_call","payload":[{"tool_name":"terminal","args":{"command":"ls"}}]}'

# 执行 hook 脚本
./docs/examples/hooks/audit_logger.sh

# 检查输出是否为有效 JSON
./docs/examples/hooks/audit_logger.sh | jq .
```

### 查看 Hook 执行日志

Hook 执行失败会在日志中记录警告：

```
WARNING  aworld.runners.hook.v2.wrappers:wrappers.py:253 Hook 'my-hook' execution failed: ...
```

## 最佳实践

1. **保持简单**: Hook 脚本应尽量简单，避免复杂逻辑
2. **幂等性**: Hook 应该可以安全地重复执行
3. **错误处理**: Hook 脚本应处理错误情况，避免崩溃
4. **性能优化**: 避免在 hook 中执行耗时操作
5. **日志记录**: 使用 stderr 输出调试信息，stdout 仅输出 JSON
6. **测试覆盖**: 为每个 hook 编写单元测试

## 进阶用法

### 异步 Hook（实验性）

```json
{
  "continue": true,
  "async": true,
  "async_task_id": "background-task-123",
  "async_timeout": 60000
}
```

### Python Callback Hook

```yaml
hooks:
  - name: "python-hook"
    hook_point: "after_tool_call"
    callback: "mypackage.hooks:audit_tool_call"
    enabled: true
```

### Hook 链式执行

多个 hook 按顺序执行，后一个 hook 接收前一个 hook 修改后的数据：

```yaml
hooks:
  - name: "sanitizer"
    hook_point: "before_tool_call"
    command: "./sanitize_input.sh"
  
  - name: "validator"
    hook_point: "before_tool_call"
    command: "./validate_input.sh"
```

## 参考资料

- [Hooks V2 设计文档](../../designs/hooks-v2/DESIGN.md)
- [HookJSONOutput 协议](../../designs/hooks-v2/PROTOCOL.md)
- [权限决策系统](../../designs/hooks-v2/PERMISSION.md)
- [测试用例](../../../tests/hooks/)
