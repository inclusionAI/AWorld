# AWorld CLI

AWorld CLI 是一个命令行工具，用于与 AWorld 代理进行交互。

## 目录结构

```
aworld_cli/
├── __init__.py          # 包初始化，导出主要接口
├── main.py              # 命令行入口点
├── console.py           # CLI UI 和交互逻辑
├── runtime/             # 运行时环境目录
│   ├── __init__.py      # 导出所有适配器
│   ├── base.py          # 基础适配器类
│   ├── local.py         # 本地适配器
│   └── remote.py        # 远程适配器
├── executors/           # 执行器目录
│   ├── __init__.py      # 导出执行器协议
│   ├── local.py         # 本地执行器
│   └── remote.py        # 远程执行器（支持流式输出）
└── models/              # 数据模型目录
    ├── __init__.py      # 导出模型协议
    └── agent_info.py    # Agent 信息模型实现
```

## 扩展指南

### 添加新的适配器

1. 在 `runtime/` 目录下创建新文件，例如 `custom.py`
2. 继承 `BaseAgentRuntime` 并实现必要的方法：

```python
from aworld_cli.runtime.base import BaseAgentRuntime
from aworld_cli.models import AgentInfo
from aworld_cli.executors import AgentExecutor

class CustomRuntime(BaseAgentRuntime):
    async def _load_agents(self) -> List[AgentInfo]:
        # 实现加载代理的逻辑
        pass
    
    async def _create_executor(self, agent: AgentInfo) -> Optional[AgentExecutor]:
        # 实现创建执行器的逻辑
        pass
    
    def _get_source_type(self) -> str:
        return "CUSTOM"
    
    def _get_source_location(self) -> str:
        return "custom://location"
```

3. 在 `runtime/__init__.py` 中导出新运行时

### 添加新的执行器

1. 在 `executors/` 目录下创建新文件
2. 实现 `AgentExecutor` 协议：

```python
from aworld_cli.executors import AgentExecutor

class CustomExecutor(AgentExecutor):
    async def chat(self, message: str) -> str:
        # 实现聊天逻辑
        pass
```

3. 在 `executors/__init__.py` 中导出新执行器

### 添加新的 Agent 信息模型

1. 在 `models/agent_info.py` 中添加新类
2. 确保实现 `AgentInfo` 协议（name, desc 属性）

## 使用示例

```python
from aworld_cli.runtime import LocalRuntime, RemoteRuntime, MixedRuntime

# 使用本地运行时
local_runtime = LocalRuntime()
await local_runtime.start()

# 使用远程运行时
remote_runtime = RemoteRuntime("http://localhost:8000")
await remote_runtime.start()

# 使用混合运行时（推荐，支持本地和远程）
mixed_runtime = MixedRuntime()
await mixed_runtime.start()
```

