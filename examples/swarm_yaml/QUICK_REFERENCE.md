# Swarm YAML 快速参考

## 快速开始

```python
from aworld.core.agent.base import Agent
from aworld.core.agent.swarm_builder import build_swarm_from_yaml

# 1. 创建智能体
agents = {
    "agent1": Agent(name="agent1"),
    "agent2": Agent(name="agent2"),
}

# 2. 从 YAML 构建
swarm = build_swarm_from_yaml("config.yaml", agents)

# 3. 使用
swarm.reset("任务描述")
```

## YAML 模板

### Workflow（工作流）

```yaml
swarm:
  type: "workflow"
  agents:
    - id: "agent1"
      next: "agent2"
    - id: "agent2"
      next: "agent3"
    - id: "agent3"
```

### Team（团队）

```yaml
swarm:
  type: "team"
  root_agent: "leader"
  agents:
    - id: "leader"
      next: ["worker1", "worker2"]
    - id: "worker1"
    - id: "worker2"
```

### Handoff（切换）

```yaml
swarm:
  type: "handoff"
  root_agent: "agent1"
  agents:
    - id: "agent1"
    - id: "agent2"
  edges:
    - from: "agent1"
      to: "agent2"
    - from: "agent2"
      to: "agent1"
```

## 节点类型速查

| 类型 | 用途 | 语法 |
|------|------|------|
| `agent` | 普通智能体 | `- id: "agent1"` |
| `parallel` | 并行执行 | `node_type: "parallel"`<br>`agents: ["a1", "a2"]` |
| `serial` | 串行执行 | `node_type: "serial"`<br>`agents: ["a1", "a2"]` |
| `swarm` | 嵌套 Swarm | `node_type: "swarm"`<br>`swarm_type: "team"` |

## 边定义速查

### 方式 1：next 语法糖

```yaml
# 单个出边
- id: "agent1"
  next: "agent2"

# 多个出边
- id: "agent1"
  next: ["agent2", "agent3"]
```

### 方式 2：显式 edges

```yaml
edges:
  - from: "agent1"
    to: "agent2"
  - from: "agent1"
    to: "agent3"
```

### 方式 3：混合使用

```yaml
agents:
  - id: "agent1"
    next: "agent2"  # 语法糖
  - id: "agent2"
  - id: "agent3"

edges:
  - from: "agent1"
    to: "agent3"  # 补充边，优先级更高
```

## 并行组

```yaml
- id: "parallel_group"
  node_type: "parallel"
  agents: ["task1", "task2", "task3"]
  next: "merge"

- id: "task1"
- id: "task2"
- id: "task3"
- id: "merge"
```

## 串行组

```yaml
- id: "serial_group"
  node_type: "serial"
  agents: ["step1", "step2", "step3"]
  next: "next_agent"

- id: "step1"
- id: "step2"
- id: "step3"
- id: "next_agent"
```

## 嵌套 Swarm

```yaml
- id: "nested_team"
  node_type: "swarm"
  swarm_type: "team"      # workflow | handoff | team
  root_agent: "leader"
  agents:
    - id: "leader"
      next: ["worker1", "worker2"]
    - id: "worker1"
    - id: "worker2"
  next: "next_agent"
```

## 完整配置选项

```yaml
swarm:
  name: "swarm_name"           # 可选
  type: "workflow"             # 必填: workflow | handoff | team
  max_steps: 10                # 可选，默认 0
  event_driven: true           # 可选，默认 true
  root_agent: "agent_id"       # 可选，workflow 可以是列表
  min_call_num: 2              # 可选，仅 team 类型
  
  agents:
    - id: "agent1"             # 必填
      node_type: "agent"       # 可选: agent | parallel | serial | swarm
      next: "agent2"           # 可选: 单个 ID 或 ID 列表
      name: "display_name"     # 可选，用于 parallel/serial/swarm
      
      # 仅 parallel/serial 需要
      agents: ["a1", "a2"]
      
      # 仅 swarm 需要
      swarm_type: "team"
      root_agent: "leader"
      max_steps: 5
      agents: [...]            # 嵌套的智能体定义
  
  edges:                       # 可选
    - from: "agent1"
      to: "agent2"
```

## 常见模式

### 线性流程

```yaml
A → B → C → D
```

```yaml
agents:
  - id: "A"
    next: "B"
  - id: "B"
    next: "C"
  - id: "C"
    next: "D"
  - id: "D"
```

### 分支合并

```yaml
     ┌→ B ─┐
A ───┤     ├→ D
     └→ C ─┘
```

```yaml
agents:
  - id: "A"
    next: ["B", "C"]
  - id: "B"
    next: "D"
  - id: "C"
    next: "D"
  - id: "D"
```

### 并行任务

```yaml
A → [B, C, D] → E
```

```yaml
agents:
  - id: "A"
    next: "parallel"
  - id: "parallel"
    node_type: "parallel"
    agents: ["B", "C", "D"]
    next: "E"
  - id: "B"
  - id: "C"
  - id: "D"
  - id: "E"
```

### 星型团队

```yaml
      ┌→ W1
M ────┼→ W2
      └→ W3
```

```yaml
swarm:
  type: "team"
  agents:
    - id: "M"
      next: ["W1", "W2", "W3"]
    - id: "W1"
    - id: "W2"
    - id: "W3"
```

## 验证检查清单

- [ ] Swarm type 是否有效？(workflow/handoff/team)
- [ ] 所有 agent 是否有唯一的 id？
- [ ] 所有引用的 agent 是否在 agents_dict 中？
- [ ] parallel/serial 节点是否有 agents 字段？
- [ ] swarm 节点是否有 swarm_type 和 agents 字段？
- [ ] workflow 是否无环？
- [ ] edges 中的 from/to 是否都有效？

## 错误排查

| 错误信息 | 原因 | 解决方法 |
|---------|------|----------|
| Agent 'xxx' not found | agents_dict 缺少智能体 | 添加到 agents_dict |
| Duplicate agent id | ID 重复 | 重命名其中一个 |
| Workflow unsupported cycle | workflow 有环 | 改用 handoff 或去除环 |
| must have 'agents' field | parallel/serial 缺少字段 | 添加 agents 列表 |

## 示例文件位置

```
examples/swarm_yaml/
├── simple_workflow.yaml      ← 最简单的例子，从这里开始
├── parallel_workflow.yaml    ← 并行执行
├── team_swarm.yaml          ← 团队模式
├── handoff_swarm.yaml       ← 切换模式
├── nested_swarm.yaml        ← 嵌套 Swarm
├── complex_workflow.yaml    ← 复杂组合
└── multi_level_nested.yaml  ← 多层嵌套
```

## API 速查

```python
# 从 YAML 文件构建
from aworld.core.agent.swarm_builder import build_swarm_from_yaml
swarm = build_swarm_from_yaml("config.yaml", agents_dict)

# 从字典构建
from aworld.core.agent.swarm_builder import build_swarm_from_dict
swarm = build_swarm_from_dict(config_dict, agents_dict)

# 验证配置
from aworld.core.agent.swarm_builder import SwarmConfigValidator
SwarmConfigValidator.validate_config(config)
```

## 提示

1. **从简单开始**：先用 `simple_workflow.yaml` 测试
2. **Agent ID 命名**：使用描述性名称（如 `data_processor` 而非 `agent1`）
3. **先验证再运行**：使用 `SwarmConfigValidator` 提前发现问题
4. **逐步添加复杂度**：先跑通简单流程，再添加并行/嵌套
5. **查看示例**：`run_example.py` 包含所有模式的可运行代码
