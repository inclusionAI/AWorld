# Swarm YAML 构建器示例

本目录包含使用 YAML 配置文件构建 AWorld Swarm 的示例。

## 概述

基于 YAML 的 Swarm 构建器提供了一种声明式的方式来定义多智能体系统，无需编写 Python 代码来构建拓扑结构。支持的特性包括：

- **三种 Swarm 类型**：Workflow（工作流）、Handoff（切换）和 Team（团队）
- **嵌套 Swarm**：在 Swarm 中嵌入 Swarm
- **并行/串行组**：表达并行和顺序执行
- **灵活的拓扑**：使用语法糖（`next`）或显式边定义

## 快速开始

### 1. 安装依赖

```bash
pip install pyyaml
```

### 2. 运行示例

```bash
cd examples/swarm_yaml
python run_example.py
```

## YAML 结构

### 基本结构

```yaml
swarm:
  name: "my_swarm"
  type: "workflow"  # workflow | handoff | team
  max_steps: 10
  event_driven: true
  root_agent: "agent1"  # 可选
  
  agents:
    - id: "agent1"
      next: "agent2"
    
    - id: "agent2"
      next: "agent3"
    
    - id: "agent3"
  
  # 可选：显式边定义（与 'next' 合并）
  edges:
    - from: "agent1"
      to: "agent2"
```

### 节点类型

#### 1. 普通 Agent（默认）

```yaml
- id: "agent1"
  node_type: "agent"  # 可以省略
  next: "agent2"
```

#### 2. 并行组

智能体并行执行（包装在 `ParallelizableAgent` 中）：

```yaml
- id: "parallel_tasks"
  node_type: "parallel"
  agents: ["task1", "task2", "task3"]
  next: "merge_agent"

- id: "task1"
- id: "task2"
- id: "task3"
```

#### 3. 串行组

智能体顺序执行（包装在 `SerialableAgent` 中）：

```yaml
- id: "serial_steps"
  node_type: "serial"
  agents: ["step1", "step2", "step3"]
  next: "next_agent"

- id: "step1"
- id: "step2"
- id: "step3"
```

#### 4. 嵌套 Swarm

在另一个 Swarm 中嵌入 Swarm（包装在 `TaskAgent` 中）：

```yaml
- id: "sub_team"
  node_type: "swarm"
  swarm_type: "team"  # workflow | handoff | team
  root_agent: "leader"
  agents:
    - id: "leader"
      next: ["worker1", "worker2"]
    - id: "worker1"
    - id: "worker2"
  next: "next_agent"
```

## 示例说明

### 示例 1：简单工作流

**文件**：`simple_workflow.yaml`

三个智能体按顺序执行的线性工作流。

```yaml
swarm:
  name: "simple_workflow"
  type: "workflow"
  
  agents:
    - id: "agent1"
      next: "agent2"
    - id: "agent2"
      next: "agent3"
    - id: "agent3"
```

### 示例 2：并行工作流

**文件**：`parallel_workflow.yaml`

演示多个智能体的并行执行。

```yaml
swarm:
  name: "parallel_workflow"
  type: "workflow"
  
  agents:
    - id: "start"
      next: "parallel_tasks"
    
    - id: "parallel_tasks"
      node_type: "parallel"
      agents: ["task1", "task2", "task3"]
      next: "merge"
    
    - id: "task1"
    - id: "task2"
    - id: "task3"
    - id: "merge"
```

### 示例 3：团队 Swarm

**文件**：`team_swarm.yaml`

一个协调器智能体管理多个工作者智能体（星型拓扑）。

```yaml
swarm:
  name: "team_example"
  type: "team"
  root_agent: "coordinator"
  
  agents:
    - id: "coordinator"
      next: ["worker1", "worker2", "worker3"]
    - id: "worker1"
    - id: "worker2"
    - id: "worker3"
```

### 示例 4：切换 Swarm

**文件**：`handoff_swarm.yaml`

智能体可以动态地将控制权切换给彼此。

```yaml
swarm:
  name: "handoff_example"
  type: "handoff"
  root_agent: "agent1"
  
  agents:
    - id: "agent1"
    - id: "agent2"
    - id: "agent3"
  
  edges:
    - from: "agent1"
      to: "agent2"
    - from: "agent1"
      to: "agent3"
    - from: "agent2"
      to: "agent3"
    - from: "agent3"
      to: "agent1"  # 允许形成环
```

### 示例 5：嵌套 Swarm

**文件**：`nested_swarm.yaml`

在工作流中嵌入一个团队 Swarm。

```yaml
swarm:
  name: "nested_swarm_example"
  type: "workflow"
  
  agents:
    - id: "preprocessor"
      next: "analysis_team"
    
    - id: "analysis_team"
      node_type: "swarm"
      swarm_type: "team"
      root_agent: "coordinator"
      agents:
        - id: "coordinator"
          next: ["analyst1", "analyst2", "analyst3"]
        - id: "analyst1"
        - id: "analyst2"
        - id: "analyst3"
      next: "summarizer"
    
    - id: "summarizer"
```

### 示例 6：复杂工作流

**文件**：`complex_workflow.yaml`

结合并行、串行和分支路径。

### 示例 7：多层嵌套

**文件**：`multi_level_nested.yaml`

多层 Swarm 嵌套。

## Python 中的使用

```python
from aworld.core.agent.base import Agent
from aworld.core.agent.swarm_builder import build_swarm_from_yaml

# 创建智能体
agents_dict = {
    "agent1": Agent(name="agent1", desc="第一个智能体"),
    "agent2": Agent(name="agent2", desc="第二个智能体"),
    "agent3": Agent(name="agent3", desc="第三个智能体"),
}

# 从 YAML 构建 Swarm
swarm = build_swarm_from_yaml("simple_workflow.yaml", agents_dict)

# 初始化并使用
swarm.reset("你的任务描述")

# 访问 Swarm 属性
print(f"Swarm 类型: {swarm.build_type}")
print(f"有序智能体: {[a.name() for a in swarm.ordered_agents]}")
```

## 语法糖：`next` vs `edges`

### 使用 `next`（推荐用于简单情况）

```yaml
agents:
  - id: "agent1"
    next: "agent2"  # 单条边
  
  - id: "agent2"
    next: ["agent3", "agent4"]  # 多条边
```

### 使用 `edges`（推荐用于复杂图）

```yaml
agents:
  - id: "agent1"
  - id: "agent2"
  - id: "agent3"

edges:
  - from: "agent1"
    to: "agent2"
  - from: "agent1"
    to: "agent3"
```

### 同时使用两者

如果同时定义了 `next` 和 `edges`：
- 两者都会合并到最终拓扑中
- 如果有冲突（相同的边定义了两次），`edges` 优先级更高

## 关键设计原则

1. **Agent ID 必须全局唯一**：所有嵌套层级的 Agent ID 必须唯一。

2. **并行/串行组引用已有智能体**：`parallel` 或 `serial` 组中的智能体必须在同一级别或外层定义。

3. **嵌套 Swarm 是透明的**：嵌套 Swarm 在父拓扑中显示为常规节点，但内部维护自己的结构。

4. **加载时验证**：配置在加载时会被验证，尽早发现错误。

## API 参考

### 主函数

```python
def build_swarm_from_yaml(
    yaml_path: str,
    agents_dict: Dict[str, BaseAgent],
    **kwargs
) -> Swarm:
    """从 YAML 配置文件构建 Swarm 实例。
    
    Args:
        yaml_path: YAML 配置文件路径。
        agents_dict: 智能体 ID 到智能体实例的映射字典。
        **kwargs: 额外参数，用于覆盖 YAML 配置。
    
    Returns:
        构建的 Swarm 实例。
    """
```

### 替代函数

```python
def build_swarm_from_dict(
    config: Dict[str, Any],
    agents_dict: Dict[str, BaseAgent],
    **kwargs
) -> Swarm:
    """从配置字典构建 Swarm 实例。
    
    当配置从其他源（JSON、数据库等）加载时很有用。
    """
```

## 验证规则

YAML 配置会按照以下规则进行验证：

1. **必填字段**：
   - `swarm.type`：必须是 `workflow`、`handoff` 或 `team` 之一
   - `swarm.agents`：必须至少有一个智能体
   - 每个智能体必须有 `id` 字段

2. **Agent ID 唯一性**：不允许重复的 Agent ID

3. **节点类型验证**：
   - `parallel` 和 `serial` 节点必须有 `agents` 字段
   - `swarm` 节点必须有 `swarm_type` 和 `agents` 字段

4. **边验证**：
   - 每条边必须有 `from` 和 `to` 字段
   - 引用的智能体必须存在

5. **Swarm 类型约束**：
   - `workflow`：不能有环（仅 DAG）
   - `handoff`：所有拓扑项必须是智能体对
   - `team`：必须指定根智能体或第一个智能体为根

## 故障排除

### 错误："Agent 'xxx' not found in agents_dict"

确保 YAML 中引用的所有智能体（包括嵌套 Swarm 中的智能体）都存在于 `agents_dict` 参数中。

### 错误："Duplicate agent id: xxx"

Agent ID 必须全局唯一。重命名其中一个冲突的智能体。

### 错误："Workflow unsupported cycle graph"

Workflow 类型不能有环。如果需要环，使用 `handoff` 类型，或重构你的拓扑。

### 错误："Swarm node must have 'swarm_type' field"

使用 `node_type: "swarm"` 时，必须指定 `swarm_type` 字段（workflow/handoff/team）。

## 最佳实践

1. **使用描述性的 Agent ID**：使 Agent ID 有意义（例如 `data_preprocessor` 而不是 `agent1`）

2. **选择正确的 Swarm 类型**：
   - 使用 `workflow` 用于确定性的顺序流程
   - 使用 `handoff` 用于动态的、AI 驱动的智能体协作
   - 使用 `team` 用于协调器-工作者模式

3. **简单拓扑优先使用 `next`**：比显式边更易读

4. **复杂图使用显式 `edges`**：当有许多交叉连接时

5. **记录你的拓扑**：在 YAML 中添加注释来解释流程

6. **尽早验证**：先用简单的智能体测试你的 YAML 配置

## 相关文档

- [AWorld Swarm 文档](../../docs/core_concepts/mas/index.html)
- [智能体文档](../../docs/Agents/)
- [Workflow vs Handoff vs Team](../../docs/Get%20Start/Core%20Capabilities.md)

## 许可证

Copyright (c) 2025 inclusionAI.
