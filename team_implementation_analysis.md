# AWorld Team实现分析报告

## 1. 概述

AWorld的Team实现是一个基于**星型拓扑（Star Topology）**的多智能体协作模式，其中一个领导者（Leader/Coordinator）智能体负责协调和调度多个执行者（Executor/Worker）智能体。

### 1.1 核心特点

- **中心化协调**：一个root_agent作为领导者，其他agents作为执行者
- **单向通信**：只有root_agent可以调用其他agents（通过handoffs）
- **工具化执行者**：执行者agents被设置为`feedback_tool_result=True`，作为工具被调用
- **动态拓扑**：支持运行时添加/删除agents
- **最小交互次数**：可配置`min_call_num`确保至少调用一次执行者

## 2. 核心类与架构

### 2.1 TeamSwarm类

**位置**: `aworld/core/agent/swarm.py:410`

```python
class TeamSwarm(Swarm):
    """Coordination paradigm."""
    
    def __init__(self,
                 *args,  # agent
                 topology: List[tuple] = None,
                 root_agent: BaseAgent = None,
                 max_steps: int = 0,
                 register_agents: List[BaseAgent] = None,
                 builder_cls: str = None,
                 event_driven: bool = True,
                 **kwargs):
        super().__init__(*args,
                         topology=topology,
                         root_agent=root_agent,
                         max_steps=max_steps,
                         register_agents=register_agents,
                         build_type=GraphBuildType.TEAM,
                         builder_cls=builder_cls,
                         event_driven=event_driven, **kwargs)
        # team minimum interactive call number
        self.min_call_num = kwargs.get("min_call_num", 0)
```

**关键特性**：
- 继承自`Swarm`基类
- 使用`GraphBuildType.TEAM`作为构建类型
- 支持`min_call_num`参数控制最小交互次数

### 2.2 TeamBuilder类

**位置**: `aworld/core/agent/swarm.py:1090`

```python
class TeamBuilder(TopologyBuilder):
    """Team mechanism requires a leadership agent, and other agents follow its command.
    
    Examples:
    >>> agent1 = Agent(name='agent1'); agent2 = Agent(name='agent2')
    >>> Swarm(agent1, agent2, agent3, build_type=GraphBuildType.TEAM)
    
    The topology means that agent1 is the leader agent, and agent2, agent3 are executors.
    """
```

**构建逻辑**：

1. **验证拓扑**（`_valid_check`）：
   - 确保root_agent不是列表
   - 如果root_agent不在topology中，自动添加到首位

2. **构建图结构**（`build`）：
   ```python
   def build(self):
       # 1. 创建AgentGraph
       agent_graph = AgentGraph(GraphBuildType.TEAM.value, root_agent=self.root_agent)
       
       # 2. 添加root_agent节点
       root_agent = self.topology[0]
       agent_graph.add_node(root_agent)
       root_agent.feedback_tool_result = True
       
       # 3. 添加执行者agents
       for agent in self.topology[1:]:
           agent.feedback_tool_result = True
           agent_graph.add_node(agent)
           agent_graph.add_edge(root_agent, agent)  # 单向边
           root_agent.handoffs.append(agent.id())
           
           # 移除自引用
           if agent.id() in agent.handoffs:
               agent.handoffs.remove(agent.id())
       
       return agent_graph
   ```

### 2.3 AgentGraph类

**位置**: `aworld/core/agent/swarm.py:561`

```python
class AgentGraph:
    """The agent's graph is a directed graph, and can update the topology at runtime."""
    
    def __init__(self,
                 build_type: str,
                 root_agent: BaseAgent = None,
                 ordered_agents: List[BaseAgent] = None,
                 agents: Dict[str, BaseAgent] = None,
                 predecessor: Dict[str, Dict[str, EdgeInfo]] = None,
                 successor: Dict[str, Dict[str, EdgeInfo]] = None):
        self.build_type = build_type
        self.ordered_agents = ordered_agents if ordered_agents else []
        self.agents: OrderedDict = agents if agents else OrderedDict()
        self.predecessor = predecessor if predecessor else {}
        self.successor = successor if successor else {}
        self.has_cycle = False
        self.root_agent = root_agent
```

**核心功能**：
- 维护agents的有向图结构
- 支持前驱/后继关系
- 支持拓扑排序
- 支持运行时动态更新

## 3. 动态拓扑管理

### 3.1 添加Agents

**方法**: `TeamSwarm.add_agents(agents, to_remove_agents)`

```python
def add_agents(self, agents: List[BaseAgent], to_remove_agents: List[BaseAgent] = None):
    # 1. 删除旧agents（如果指定）
    if not to_remove_agents:
        to_remove_agents = agents
    self.del_agents(to_remove_agents)
    
    # 2. 添加新agents
    if agents_to_add:
        super().add_agents(agents_to_add)
        
        for agent in agents_to_add:
            # 添加到图中
            self.agent_graph.add_node(agent)
            
            # 添加从root_agent到agent的边
            root_agent = self.agent_graph.root_agent
            self.agent_graph.add_edge(root_agent, agent)
            
            # 注册handoff
            root_agent.handoffs.append(agent.id())
            
            # 设置为工具模式
            agent.feedback_tool_result = True
            
            # 移除自引用
            if agent.id() in agent.handoffs:
                agent.handoffs.remove(agent.id())
            
            self.topology.append(agent)
```

### 3.2 删除Agents

**方法**: `TeamSwarm.del_agents(agents)`

```python
def del_agents(self, agents: List[BaseAgent]):
    for agent in agents_to_remove:
        # 1. 从agent_graph中移除
        self.agent_graph.del_node(agent)
        
        # 2. 从root_agent的handoffs中移除
        root_agent.handoffs.remove(agent.id())
        
        # 3. 从topology列表中移除
        if agent in self.topology:
            self.topology.remove(agent)
        
        # 4. 从register_agents中移除
        if agent in self.register_agents:
            self.register_agents.remove(agent)
        
        # 5. 重置agent属性
        agent.feedback_tool_result = False
```

**安全机制**：
- 不允许删除root_agent
- 只删除实际存在的agents
- 完整清理所有引用

## 4. Team成员注册机制

### 4.1 SubagentManager集成

**位置**: `aworld/core/agent/subagent_manager.py:99`

```python
async def register_team_members(self, swarm: 'Swarm'):
    """
    Register TeamSwarm members as available subagents (thread-safe, idempotent).
    
    This method is automatically called when an agent with enable_subagent=True
    runs in a TeamSwarm context. All team members (except self) are registered
    as callable subagents.
    """
    async with self._registry_lock:
        # 幂等性检查
        if self._registered:
            return
        
        # 注册所有team成员（除了自己）
        for agent_id, agent in swarm.agents.items():
            if agent.id() == self.agent.id():
                continue
            
            # 创建SubagentInfo
            subagent_info = SubagentInfo(
                name=agent.name(),
                description=agent.desc() if hasattr(agent, 'desc') else "",
                source='team_member',
                tools=agent.tool_names if hasattr(agent, 'tool_names') else [],
                agent_instance=agent,
                config=None
            )
            
            self._available_subagents[agent.name()] = subagent_info
        
        self._registered = True
```

**特点**：
- **线程安全**：使用asyncio.Lock
- **幂等性**：只注册一次
- **自动发现**：自动注册所有team成员
- **排除自己**：避免递归调用

## 5. 使用示例

### 5.1 基本用法

```python
from aworld.core.agent.swarm import TeamSwarm
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig

# 创建agents
coordinator = Agent(name="Coordinator", conf=AgentConfig())
worker1 = Agent(name="Worker1", conf=AgentConfig())
worker2 = Agent(name="Worker2", conf=AgentConfig())

# 方式1：第一个agent自动成为root_agent
swarm = TeamSwarm(coordinator, worker1, worker2)

# 方式2：显式指定root_agent
swarm = TeamSwarm(worker1, worker2, root_agent=coordinator)

# 运行
result = await Runners.run(input="任务描述", swarm=swarm)
```

### 5.2 Master-Worker模式

**示例**: `examples/multi_agents/coordination/master_worker/run.py`

```python
def get_single_action_team_swarm(user_input):
    # 创建planning Agent（领导者）
    plan_agent = Agent(
        name="plan_agent",
        desc="Agent responsible for deciding whether to execute search or summary",
        conf=agent_config,
        system_prompt=plan_sys_prompt,
        use_planner=False,
        use_tools_in_prompt=False
    )
    
    # 创建search Agent（执行者）
    search_agent = Agent(
        name="search_agent",
        desc="Agent responsible for executing web search tasks",
        conf=agent_config,
        agent_config=search_sys_prompt,
        tool_names=[Tools.SEARCH_API.value]
    )
    
    # 创建summary Agent（执行者）
    summary_agent = Agent(
        name="summary_agent",
        desc="Agent responsible for summarizing information",
        conf=agent_config,
        agent_config=summary_sys_prompt,
    )
    
    # 创建TeamSwarm
    return TeamSwarm(plan_agent, search_agent, summary_agent, max_steps=10)
```

### 5.3 动态添加/删除成员

```python
# 创建初始team
swarm = TeamSwarm(coordinator, worker1)

# 添加新worker
new_worker = Agent(name="Worker3", conf=AgentConfig())
swarm.add_agents([new_worker])

# 删除worker
swarm.del_agents([worker1])
```

## 6. 与其他模式的对比

### 6.1 Team vs Workflow vs Handoff

| 特性 | Team | Workflow | Handoff |
|------|------|----------|---------|
| **拓扑结构** | 星型（Star） | 链式/DAG | 任意图 |
| **协调方式** | 中心化 | 顺序执行 | 去中心化 |
| **通信模式** | Root→Executors | Agent1→Agent2→... | Agent↔Agent |
| **适用场景** | 任务分发、并行执行 | 流水线处理 | 协作决策 |
| **循环支持** | 否 | 否 | 是 |
| **动态性** | 高（可动态添加/删除） | 低 | 中 |

### 6.2 Team vs Hybrid

**Hybrid模式** = Team（星型拓扑）+ Peer通信（执行者间可通信）

```python
class HybridBuilder(TeamBuilder):
    """Hybrid mechanism combines hierarchical oversight (TeamSwarm) 
    with peer-to-peer coordination."""
    
    def build(self):
        # Step 1: 构建基础星型拓扑
        agent_graph = super().build()
        
        # Step 2: 添加执行者间的通信能力
        # (通过EventManager实现peer-to-peer通信)
        agent_graph.build_type = GraphBuildType.HYBRID.value
        
        return agent_graph
```

## 7. 关键设计决策

### 7.1 为什么使用feedback_tool_result=True？

```python
agent.feedback_tool_result = True
```

**原因**：
- 将执行者agents标记为"工具"
- 使得root_agent可以像调用工具一样调用执行者
- 执行结果会反馈给root_agent，而不是直接返回给用户

### 7.2 为什么只有单向边？

```python
agent_graph.add_edge(root_agent, agent)  # 只添加root→agent的边
```

**原因**：
- Team模式是中心化协调，执行者不应该调用其他agents
- 避免循环依赖
- 简化控制流

### 7.3 为什么需要min_call_num？

```python
self.min_call_num = kwargs.get("min_call_num", 0)
```

**原因**：
- 确保至少与一个执行者交互
- 防止root_agent直接返回结果而不调用任何执行者
- 默认为0（至少调用一次）

## 8. 测试覆盖

### 8.1 单元测试

**位置**: `tests/core/test_swarm_regression.py:65`

```python
class TestTeamSwarmRegression:
    def test_team_basic_creation(self):
        """Test basic team swarm creation."""
        # 验证build_type
        # 验证星型拓扑
        # 验证handoffs
    
    def test_team_with_root_agent_param(self):
        """Test team swarm with explicit root_agent parameter."""
        # 验证显式root_agent参数
```

### 8.2 集成测试

**位置**: `tests/core/agent/test_subagent_manager.py:147`

```python
class TestTeamMemberRegistration:
    async def test_register_team_members_basic(self):
        """Test basic team member registration."""
    
    async def test_register_team_members_idempotent(self):
        """Test idempotent registration."""
    
    async def test_register_team_members_excludes_self(self):
        """Test self-exclusion in registration."""
```

## 9. 最佳实践

### 9.1 Root Agent设计

```python
# ✅ 好的做法：Root agent专注于协调
plan_agent = Agent(
    name="plan_agent",
    desc="Coordinator that delegates tasks",
    system_prompt="You are a coordinator. Analyze the task and delegate to workers.",
    use_planner=False,  # 不需要复杂规划
    use_tools_in_prompt=False  # 不需要自己的工具
)

# ❌ 不好的做法：Root agent也执行具体任务
plan_agent = Agent(
    name="plan_agent",
    tool_names=['search', 'calculator', ...]  # 应该委托给workers
)
```

### 9.2 Worker Agent设计

```python
# ✅ 好的做法：Worker专注于特定任务
search_agent = Agent(
    name="search_agent",
    desc="Specialized in web search",
    tool_names=[Tools.SEARCH_API.value],  # 只有搜索工具
    system_prompt="You are a search specialist. Execute search queries accurately."
)

# ❌ 不好的做法：Worker功能过于宽泛
worker = Agent(
    name="worker",
    tool_names=['search', 'calculator', 'browser', ...]  # 太多工具
)
```

### 9.3 Max Steps配置

```python
# ✅ 好的做法：根据任务复杂度设置
swarm = TeamSwarm(
    coordinator, 
    worker1, 
    worker2, 
    max_steps=10  # 允许多轮交互
)

# ❌ 不好的做法：max_steps太小
swarm = TeamSwarm(
    coordinator, 
    worker1, 
    worker2, 
    max_steps=1  # 可能不够完成任务
)
```

## 10. 常见问题

### 10.1 如何选择Team vs Workflow？

**使用Team当**：
- 需要根据任务动态选择执行者
- 执行者可以并行工作
- 需要运行时添加/删除agents

**使用Workflow当**：
- 任务流程固定
- 需要严格的顺序执行
- 每个步骤依赖前一步的结果

### 10.2 Root Agent可以是执行者吗？

**可以**，但不推荐：
```python
# 可以工作，但不是最佳实践
coordinator = Agent(
    name="coordinator",
    tool_names=['search'],  # 既协调又执行
)
swarm = TeamSwarm(coordinator, worker1, worker2)
```

**推荐做法**：
- Root agent专注于协调
- 具体任务委托给workers

### 10.3 如何处理Worker失败？

```python
# Worker agent应该有错误处理
worker = Agent(
    name="worker",
    system_prompt="""
    If you encounter an error:
    1. Log the error details
    2. Return a clear error message
    3. Suggest alternative approaches
    """
)
```

## 11. 未来改进方向

### 11.1 优先级队列

```python
# 当前：所有workers平等
# 未来：支持优先级
swarm = TeamSwarm(
    coordinator,
    (worker1, priority=1),
    (worker2, priority=2)
)
```

### 11.2 负载均衡

```python
# 当前：手动选择worker
# 未来：自动负载均衡
swarm = TeamSwarm(
    coordinator,
    worker1,
    worker2,
    load_balancing=True
)
```

### 11.3 Worker池

```python
# 当前：固定workers
# 未来：动态worker池
swarm = TeamSwarm(
    coordinator,
    worker_pool=WorkerPool(
        worker_type=SearchAgent,
        min_workers=2,
        max_workers=10
    )
)
```

## 12. 总结

AWorld的Team实现提供了一个**灵活、可扩展的中心化协调模式**，适合以下场景：

✅ **适合**：
- 任务分发和并行执行
- 动态选择执行者
- 需要运行时调整拓扑
- Master-Worker模式

❌ **不适合**：
- 需要执行者间直接通信（使用Hybrid）
- 固定流程的顺序执行（使用Workflow）
- 复杂的协作决策（使用Handoff）

**核心优势**：
1. **简单直观**：星型拓扑易于理解和调试
2. **动态灵活**：支持运行时添加/删除agents
3. **自动发现**：与SubagentManager集成，自动注册team成员
4. **工具化执行**：执行者作为工具被调用，结果反馈给协调者

**设计哲学**：
- **关注点分离**：协调者负责决策，执行者负责执行
- **可组合性**：可以嵌套使用（TaskAgent包装Swarm）
- **渐进式增强**：从简单的Team到复杂的Hybrid
