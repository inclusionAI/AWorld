# Sandbox tool servers - 启动脚本

## 一键启动内置工具服务（filesystem、terminal）

```bash
# 在项目根或任意处执行（脚本会解析 tool_servers 路径）
./aworld/sandbox/script/start_tool_servers.sh
# 或
bash aworld/sandbox/script/start_tool_servers.sh
```

## 环境变量

| 变量 | 说明 | 默认（未设置时） |
|------|------|------------------|
| `AWORLD_FILESYSTEM_ENDPOINT` | 文件系统 MCP 服务 URL | `http://127.0.0.1:8084` |
| `AWORLD_TERMINAL_ENDPOINT`   | 终端 MCP 服务 URL     | `http://127.0.0.1:8081` |
| `AWORLD_FILESYSTEM_TOKEN`    | 文件系统认证 Token（可选） | - |
| `AWORLD_TERMINAL_TOKEN`      | 终端认证 Token（可选）   | - |
| `AWORLD_WORKSPACE`           | 工作目录（可选）         | `~/workspace` |

脚本会校验 endpoint 是否已设置；未设置则使用上述默认 URL 并导出，便于本地一键启动后 Sandbox 直接使用。

## 与 Sandbox 配合

1. 先启动服务：`./aworld/sandbox/script/start_tool_servers.sh`
2. 脚本会导出或已有 env：`AWORLD_FILESYSTEM_ENDPOINT`、`AWORLD_TERMINAL_ENDPOINT`
3. 在同一 shell 或已 source 的环境中创建 Sandbox，Sandbox 会从 env 读取 URL/TOKEN，生成 `mcp_config` 并连接

本地与远程共用同一套配置方式，仅 endpoint（和可选 token）不同。
