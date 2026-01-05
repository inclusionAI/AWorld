## 简介

Environment（环境）是 AWorld 框架中的核心概念，为智能体提供安全、隔离的工具执行环境。Environment 统一管理 MCP（Model Context Protocol）服务器，使智能体能够无缝使用各种外部工具和服务。

在 AWorld 框架中，`Sandbox` 是 Environment 的一个客户端实现，提供了 Environment 接口的具体实现。通过 `Sandbox` 类，你可以创建和管理 Environment 实例。

### 主要特性

+ ✅ **MCP 服务器管理**：统一管理多个 MCP 服务器连接
+ ✅ **工具自动发现**：自动发现并注册 MCP 工具
+ ✅ **连接池管理**：智能缓存服务器连接，提升性能
+ ✅ **注册中心集成**：支持本地和远程工具注册中心
+ ✅ **流式响应**：支持工具结果的流式返回，包括进度回调
+ ✅ **环境上下文能力**：自动注入用户定义的上下文参数到工具调用
+ ✅ **Agent 运行支持**：支持 Agent 在 Environment 环境中运行

---

## 快速开始

### 安装

Environment 模块已包含在 AWorld 框架中，无需单独安装。

```bash
pip install aworld
```

### 最简单的示例

```python
import asyncio
from aworld.sandbox import Sandbox

# 配置 MCP 服务器
mcp_config = {
    "mcpServers": {
        "gaia-mcp": {
            "type": "streamable-http",
            "url": "https://mcp.example.com/mcp",
            "headers": {
                "Authorization": "Bearer YOUR_TOKEN"
            }
        }
    }
}

async def main():
    # 创建 Environment 实例
    sandbox = Sandbox(mcp_config=mcp_config)
    
    # 列出可用工具
    tools = await sandbox.list_tools()
    print(f"可用工具数量: {len(tools)}")
    
    # 清理资源
    await sandbox.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
```
