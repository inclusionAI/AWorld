# AWorld Subagent 架构设计文档

**文档版本**: 1.3  
**创建日期**: 2026-04-07  
**更新日期**: 2026-04-07  
**作者**: Claude (feat/subagent-optimization)  
**状态**: Final Design (Codex Reviewed + Trajectory Integration)

---

## 执行摘要

本设计提出在aworld框架中实现轻量级Subagent机制，使LLM能够在运行时**自主决策**委托子任务给专门的subagent。

**⚠️ 关键发现（v1.2更新）**：
- ✅ **上下文隔离机制可复用**：aworld的`build_sub_context()`适合subagent
- ❌ **Token merge存在重复计数bug**：需先修复再实施（见§3.1.1）
- ❌ **Agent实例状态并发不安全**：需Per-spawn克隆机制（见§3.2.3）
- ❌ **工具访问控制不一致**：TeamSwarm成员绕过过滤（见§3.2.4）

**实施周期调整**：7-13.5周 → **9-15周**（含基础设施修复）

**核心价值**：
- LLM自主orchestration（vs硬编码流程）
- 上下文继承与隔离平衡（共享workspace，独立执行状态）
- 零配置扩展（添加agent.md即可）
- 自动发现机制（TeamSwarm成员 + agent.md扫描）

---

## 1. 问题陈述与动机

### 1.1 当前痛点

**场景1：GAIA benchmark agent的上下文爆炸**
```python
# 当前实现：单个agent处理所有能力
class GAIAAgent(Agent):
    def __init__(self):
        super().__init__(
            tool_names=['search', 'calculator', 'web_browser', 'python_repl', ...]
        )  # 所有工具塞在一个agent，context window容易爆
```

**场景2：固定流程 vs 灵活orchestration**
```python
# 当前方式1：硬编码流程（Runners.run）
result1 = await Runners.run(input="搜索", agent=researcher)
result2 = await Runners.run(input=f"分析{result1}", agent=analyst)
# 问题：流程固定，无法适应不同任务类型

# 当前方式2：handoffs（AI选择下一个agent）
agent1 = Agent(agent_names=['agent2', 'agent3'])
# 问题：agent1退出，无法继续处理结果
```

**场景3：用户扩展困难**
- 用户想添加新专家agent → 需要改代码重新创建Swarm
- 无法动态发现和调用新增的agent

### 1.2 参考实现：Claude Code Subagent

Claude Code提供了轻量级subagent机制：
- **Fork模式**：子代理继承父上下文（prompt cache共享）
- **工具访问控制**：白名单+黑名单机制
- **模型继承**：`model: inherit`策略
- **YAML frontmatter**：用户友好的配置格式

### 1.3 设计目标

**主要目标**：
1. 让LLM能够**自主决策**何时调用哪个subagent（Tool模式）
2. 支持两种subagent来源：
   - TeamSwarm已有成员（预定义agent）
   - agent.md动态加载（用户扩展）
3. 上下文继承：共享workspace/config，独立执行状态
4. 工具访问控制：限制subagent的工具权限

**非目标**（Phase 1）：
- ❌ 不替代现有Swarm机制（两者互补）
- ❌ 不支持跨机器/跨进程的subagent（本地执行）
- ❌ 不支持Docker容器隔离（逻辑隔离即可）
- ❌ 不支持Worktree隔离（可选，Phase 2+）

---

## 2. 核心概念与术语

### 2.1 Subagent vs 其他机制

| 机制 | 用途 | 生命周期 | 上下文 | 调用方式 |
|------|------|---------|--------|---------|
| **Swarm** | 多agent拓扑结构 | 持久化，跨任务 | 独立context | 编排 |
| **Handoffs** | AI选择下一个agent | agent间流转 | context流转 | AI决策 |
| **Runners.run()** | 独立任务执行 | 单次任务 | 全新context | 编程调用 |
| **Subagent** | 子任务委托 | 临时，单次调用 | 继承父context | LLM Tool |

**类比**：
- Swarm = 微服务架构（持久化服务）
- Subagent = 函数调用（临时执行，返回结果）

### 2.2 核心组件

```
┌─────────────────────────────────────────────────────────┐
│                      Parent Agent                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │          SubagentManager                          │  │
│  │  - 注册TeamSwarm成员                              │  │
│  │  - 扫描agent.md文件                               │  │
│  │  - 生成system_prompt片段                         │  │
│  │  - 执行subagent调用                              │  │
│  └──────────────────────────────────────────────────┘  │
│                           ↓                              │
│              spawn_subagent(name, directive)             │
│                           ↓                              │
│  ┌──────────────────────────────────────────────────┐  │
│  │  context.build_sub_context() ✅ 复用现有机制    │  │
│  │    - 创建独立TaskWorkingState                    │  │
│  │    - 继承workspace/config                        │  │
│  │    - 深拷贝kv_store                              │  │
│  │    - 记录sub_task_list                           │  │
│  └──────────────────────────────────────────────────┘  │
│                           ↓                              │
│  ┌──────────────────────────────────────────────────┐  │
│  │      Subagent执行（临时agent实例）              │  │
│  │    - 独立working_state                           │  │
│  │    - 工具过滤（白名单+黑名单）                   │  │
│  │    - 模型继承（inherit策略）                     │  │
│  └──────────────────────────────────────────────────┘  │
│                           ↓                              │
│  ┌──────────────────────────────────────────────────┐  │
│  │  context.merge_sub_context() ✅ 复用现有机制    │  │
│  │    - 合并kv_store                                │  │
│  │    - 更新sub_task_list状态                       │  │
│  │    - 累加token_usage                             │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## 3. 架构设计

### 3.1 关键发现：复用现有机制

**aworld已有完善的task/subtask上下文隔离机制**，无需从头实现：

```python
# ✅ 已有：创建独立子上下文
sub_context = await context.build_sub_context(
    sub_task_content=directive,
    sub_task_id=f"{name}_{uuid}"
)

# ✅ 已有：合并结果
context.merge_sub_context(sub_context)

# ✅ 已有：Agent状态隔离
agent_states: Dict[str, ApplicationAgentState]

# ✅ 已有：任务追踪
sub_task_list: List[SubTask]
```

#### 3.1.1 ⚠️ 关键问题：Token重复计数Bug

**问题发现**（Codex Adversarial Review）：

`ApplicationContext.merge_sub_context()` 存在token重复计数bug：

```python
# aworld/core/context/amni/__init__.py:900-932
def merge_sub_context(self, sub_task_context: 'ApplicationContext', **kwargs):
    super().merge_sub_context(sub_task_context)  # ❌ 已累加net increment
    
    # ... 其他合并逻辑
    
    # merge token
    self.add_token(sub_task_context.token_usage)  # ❌ 又累加total usage！
```

**根本原因**：
1. 父类`Context.merge_context()`（`base.py:411-431`）已经计算并累加net increment
2. 子类又调用`self.add_token(sub_task_context.token_usage)`，把total usage再加一次
3. 导致每次subagent调用都会虚高token计数

**影响范围**：
- 所有使用`build_sub_context()`/`merge_sub_context()`的场景
- Subagent实施后会放大问题（频繁调用merge）
- 直接影响：成本统计、benchmark对比、token budget控制

**修复方案**：
```python
# 方案A：移除重复累加（推荐）
def merge_sub_context(self, sub_task_context: 'ApplicationContext', **kwargs):
    super().merge_sub_context(sub_task_context)  # 已累加net increment
    
    # merge sub task kv_store
    # ... 其他合并逻辑
    
    # ✅ 移除重复的token累加
    # self.add_token(sub_task_context.token_usage)  # 删除这行

# 方案B：父类merge时跳过token（不推荐，影响范围大）
```

**实施要求**：
- **Phase 0（新增）**：修复token merge bug并验证（1周）
- 在所有现有subtask场景验证修复正确性
- 添加单元测试防止regression
- **Subagent Phase 1依赖此修复完成**

#### 3.1.2 Subagent特定实现

**在token merge修复后，Subagent实现需要：**
1. 封装现有机制为 `spawn_subagent()` API
2. 实现工具访问控制（工具过滤逻辑）
3. 实现模型继承（inherit策略）
4. 实现SubagentManager（自动发现+注册）
5. **新增**：Per-spawn agent实例克隆机制（见§3.2.3）
6. **新增**：统一工具过滤（TeamSwarm + agent.md）（见§3.2.4）

### 3.2 核心组件设计

#### 3.2.1 SubagentManager

```python
# aworld/core/agent/subagent_manager.py

class SubagentInfo:
    """Subagent元信息"""
    name: str
    description: str
    source: Literal['team_member', 'agent_md']  # 来源
    tools: List[str]
    agent_instance: Optional[Agent] = None  # TeamSwarm成员
    config: Optional[dict] = None           # agent.md配置

class SubagentManager:
    """管理agent可用的subagent（线程安全）"""
    
    def __init__(self, agent: Agent):
        self.agent = agent
        self._available_subagents: Dict[str, SubagentInfo] = {}
        self._registry_lock = asyncio.Lock()  # 🆕 并发安全：保护注册操作
        self._registered = False  # 🆕 避免重复注册
    
    async def register_team_members(self, swarm: Swarm):
        """注册TeamSwarm成员为可用subagent（线程安全）"""
        async with self._registry_lock:  # 🆕 锁保护
            if self._registered:  # 🆕 幂等性检查
                return
            
            for member in swarm.agents:
                if member.id() != self.agent.id():
                    self._available_subagents[member.name()] = SubagentInfo(
                        name=member.name(),
                        description=member.desc(),
                        source='team_member',
                        agent_instance=member,
                        tools=member.tool_names
                    )
            
            self._registered = True
    
    async def scan_agent_md_files(self, search_paths: List[str] = None):
        """扫描agent.md文件（线程安全 + 错误处理）"""
        if not search_paths:
            search_paths = ['./.claude/agents', '~/.claude/agents', './agents']
        
        async with self._registry_lock:  # 🆕 锁保护
            for path in search_paths:
                path_obj = Path(path).expanduser()
                if not path_obj.exists():
                    logger.debug(f"Subagent search path not found: {path}")
                    continue
                
                for md_file in path_obj.glob('*.md'):
                    try:
                        config = parse_markdown_agent(md_file)
                        if config and hasattr(config, 'name') and config.name:
                            self._available_subagents[config.name] = SubagentInfo(
                                name=config.name,
                                description=config.description or "",
                                source='agent_md',
                                config=config,
                                tools=getattr(config, 'tool_names', [])
                            )
                            logger.info(f"Registered subagent from {md_file}: {config.name}")
                        else:
                            logger.warning(f"Invalid agent.md (missing name): {md_file}")
                    except Exception as e:
                        logger.error(f"Failed to parse agent.md {md_file}: {e}", exc_info=True)
                        # 继续扫描其他文件，不crash
    
    def generate_system_prompt_section(self, max_subagents: int = 10) -> str:
        """生成system_prompt片段（告诉LLM有哪些subagent）
        
        Args:
            max_subagents: 最大显示数量，避免prompt过长。默认10个。
        
        Returns:
            System prompt片段
        """
        # 🆕 并发安全：快照读取（避免在遍历时被修改）
        subagents_snapshot = dict(self._available_subagents)
        
        if not subagents_snapshot:
            return ""
        
        prompt = "\n## Available Subagents\n\n"
        prompt += "You can delegate subtasks using the spawn_subagent tool:\n\n"
        
        # 🆕 长度限制：避免prompt爆炸
        subagents_to_show = list(subagents_snapshot.items())[:max_subagents]
        
        for name, info in subagents_to_show:
            prompt += f"- **{name}**: {info.description}\n"
            prompt += f"  - Tools: {', '.join(info.tools[:5])}"  # 🆕 工具列表也限制长度
            if len(info.tools) > 5:
                prompt += f" (+{len(info.tools) - 5} more)"
            prompt += "\n"
            prompt += f"  - Usage: spawn_subagent(name='{name}', directive='...')\n\n"
        
        # 🆕 如果有更多subagent，提示总数
        if len(subagents_snapshot) > max_subagents:
            remaining = len(subagents_snapshot) - max_subagents
            prompt += f"*({remaining} more subagents available. Use spawn_subagent with exact name.)*\n"
        
        return prompt
    
    async def spawn(self, name: str, directive: str, **kwargs) -> str:
        """执行subagent调用（核心逻辑）
        
        🆕 并发安全说明：
        - spawn()本身支持并发调用（只读_available_subagents）
        - 每个spawn创建独立的sub_context（contextvars隔离）
        - 合并操作由context.merge_sub_context()保证原子性
        
        Args:
            name: Subagent名称
            directive: 任务指令
            **kwargs: 可选参数（model, tools等）
        
        Returns:
            Subagent执行结果
        
        Raises:
            ValueError: Subagent不存在
        """
        # 🆕 审计日志：开始
        logger.info(
            f"Subagent spawn started: parent={self.agent.id()}, "
            f"subagent={name}, directive={directive[:100]}..."
        )
        
        # 🆕 并发安全：快照读取subagent信息
        if name not in self._available_subagents:
            available = ', '.join(self._available_subagents.keys())
            logger.error(f"Subagent not found: {name}. Available: {available}")
            raise ValueError(f"Subagent '{name}' not found. Available: {available}")
        
        info = self._available_subagents[name]
        context = BaseAgent._get_current_context()
        
        if not context:
            raise RuntimeError("No active context found")
        
        start_time = time.time()
        
        try:
            # 1. 创建子上下文（✅ 复用现有机制，contextvars自动隔离）
            sub_context = await context.build_sub_context(
                sub_task_content=directive,
                sub_task_id=f"{name}_{uuid.uuid4().hex[:6]}"
            )
            
            # 2. 根据来源执行（⚠️ 修复：Per-spawn克隆）
            if info.source == 'team_member':
                # ❌ 旧方案：直接复用实例（并发不安全）
                # result = await info.agent_instance.async_run(...)
                
                # ✅ 新方案：Per-spawn克隆实例（避免状态竞争）
                cloned_agent = self._clone_agent_instance(
                    info.agent_instance, 
                    filtered_tools=self._filter_tools(
                        parent_tools=self.agent.tool_names,
                        subagent_tools=info.tools,
                        disallowed=kwargs.get('disallowedTools', [])
                    )
                )
                result = await cloned_agent.async_run(
                    Message(payload=directive, context=sub_context)
                )
            else:
                # 从agent.md创建临时实例（已包含工具过滤）
                temp_agent = self._create_temp_agent(info.config, **kwargs)
                result = await temp_agent.async_run(
                    Message(payload=directive, context=sub_context)
                )
            
            # 3. 合并子上下文（✅ 复用现有机制）
            context.merge_sub_context(sub_context)
            
            # 4. 合并Subagent Trajectory到Parent（策略A）
            # 目的：支持meta-learning和reflection分析完整执行路径
            subagent_agent = cloned_agent if info.source == 'team_member' else temp_agent
            
            if hasattr(subagent_agent, 'trajectory') and subagent_agent.trajectory:
                # 构造parent trajectory item
                # (INPUT, metadata, AgentResult)
                parent_trajectory_item = (
                    directive,  # subagent的输入指令
                    {
                        'subagent_name': name,
                        'subagent_source': info.source,
                        'tools_used': info.tools,
                        'elapsed': time.time() - start_time,
                        'tokens': sub_context.token_usage
                    },
                    AgentResult(
                        current_state=result.payload,
                        actions=subagent_agent.trajectory,  # 🆕 嵌套subagent的actions
                        is_call_tool=True
                    )
                )
                
                # 添加到parent agent的trajectory
                if hasattr(self.agent, 'trajectory'):
                    self.agent.trajectory.append(parent_trajectory_item)
                
                logger.debug(
                    f"Merged subagent trajectory: {name}, "
                    f"subagent_steps={len(subagent_agent.trajectory)}, "
                    f"parent_total_steps={len(self.agent.trajectory)}"
                )
            
            # 🆕 审计日志：成功
            elapsed = time.time() - start_time
            logger.info(
                f"Subagent spawn completed: {name}, "
                f"tokens={sub_context.token_usage}, "
                f"elapsed={elapsed:.2f}s"
            )
            
            return result.payload
            
        except Exception as e:
            # 🆕 审计日志：失败
            elapsed = time.time() - start_time
            logger.error(
                f"Subagent spawn failed: {name}, "
                f"error={type(e).__name__}: {str(e)}, "
                f"elapsed={elapsed:.2f}s",
                exc_info=True
            )
            raise
    
    def _clone_agent_instance(self, original: Agent, filtered_tools: List[str]) -> Agent:
        """克隆agent实例（避免并发状态竞争）
        
        ⚠️ 关键问题修复（Codex Review）：
        BaseAgent实例存储可变状态（trajectory, tools, state, loop_step, _finished）。
        直接复用同一实例并发调用会导致状态互相污染。
        
        解决方案：Per-spawn克隆新实例，独立的运行时状态。
        
        Args:
            original: 原始TeamSwarm成员agent
            filtered_tools: 过滤后的工具列表（最小权限）
        
        Returns:
            克隆的agent实例（干净状态）
        """
        # 复制基础配置（不可变部分）
        cloned = original.__class__(
            name=original.name(),
            conf=original.conf.copy() if hasattr(original.conf, 'copy') else original.conf,
            desc=original.desc(),
            tool_names=filtered_tools,  # ✅ 应用工具过滤
            agent_names=original.handoffs.copy(),
            mcp_servers=original.mcp_servers.copy(),
            black_tool_actions=original.black_tool_actions.copy(),
            feedback_tool_result=original.feedback_tool_result,
            wait_tool_result=original.wait_tool_result,
            sandbox=original.sandbox  # Sandbox可共享（无状态）
        )
        
        # ✅ 不复制可变状态：
        # - trajectory: 空列表（subagent从空开始记录）
        #   ⚠️ trajectory处理策略：
        #   - Subagent执行期间：独立记录自己的trajectory
        #   - Spawn完成后：合并到parent agent的trajectory（见spawn()方法）
        #   - 目的：支持meta-learning和reflection分析完整执行路径
        # - tools/tool_mapping: 由agent初始化重建
        # - state: START（初始状态）
        # - loop_step: 0（重置计数器）
        # - _finished: True（初始值）
        
        return cloned
    
    def _create_temp_agent(self, config: dict, **kwargs) -> Agent:
        """从agent.md配置创建临时agent"""
        # 工具过滤
        tools = kwargs.get('tools', config.tool_names)
        allowed_tools = self._filter_tools(
            parent_tools=self.agent.tool_names,
            subagent_tools=tools,
            disallowed=config.disallowedTools
        )
        
        # 模型继承
        model = kwargs.get('model', config.model)
        if model == 'inherit':
            model = self.agent.conf.llm_model_name
        
        return Agent(
            name=config.name,
            system_prompt=config.description,
            tool_names=allowed_tools,
            mcp_servers=config.mcp_servers,
            conf=AgentConfig(llm_model_name=model)
        )
    
    def _filter_tools(
        self, 
        parent_tools: List[str], 
        subagent_tools: List[str], 
        disallowed: List[str]
    ) -> List[str]:
        """工具访问控制"""
        # 白名单逻辑
        if subagent_tools == ['*']:
            allowed = parent_tools
        else:
            allowed = [t for t in subagent_tools if t in parent_tools]
        
        # 黑名单过滤
        return [t for t in allowed if t not in disallowed]
```

#### 3.2.2 Agent增强

```python
# aworld/agents/llm_agent.py

class Agent(BaseAgent):
    def __init__(
        self,
        name: str,
        system_prompt: str = None,
        enable_subagent: bool = True,  # 🆕 是否启用subagent
        subagent_search_paths: List[str] = None,  # 🆕 agent.md扫描路径
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        
        self._original_system_prompt = system_prompt
        self.subagent_manager = None
        
        if enable_subagent:
            self.subagent_manager = SubagentManager(self)
            
            # 扫描agent.md（如果指定）
            if subagent_search_paths:
                self.subagent_manager.scan_agent_md_files(subagent_search_paths)
            
            # 注册spawn_subagent工具
            if 'spawn_subagent' not in self.tool_names:
                self.tool_names.append('spawn_subagent')
    
    def _build_system_prompt(self) -> str:
        """构建完整system_prompt（动态添加subagent列表）"""
        prompt = self._original_system_prompt or ""
        
        if self.subagent_manager:
            # 🆕 动态添加可用subagent列表
            prompt += "\n\n" + self.subagent_manager.generate_system_prompt_section()
        
        return prompt
    
    async def async_run(self, message: Message, **kwargs) -> Message:
        # 🆕 如果在Swarm中，自动注册team members（线程安全）
        if self.subagent_manager and message.context:
            swarm = getattr(message.context, 'swarm', None)
            if swarm and not self.subagent_manager._registered:
                await self.subagent_manager.register_team_members(swarm)
        
        return await super().async_run(message, **kwargs)
```

#### 3.2.3 Per-Spawn Agent实例克隆（并发安全修复）

**问题根源**（Codex Adversarial Review）：

BaseAgent存储可变实例状态，直接复用会导致并发状态竞争：

```python
# aworld/core/agent/base.py:163-181
class BaseAgent:
    def __init__(self, ...):
        self.trajectory: List[...] = []       # ❌ 实例级别
        self.tools = []                       # ❌ 实例级别
        self.state = AgentStatus.START        # ❌ 实例级别
        self._finished = True                 # ❌ 实例级别
        self.loop_step = 0                    # ❌ 实例级别
        # Contextvars只隔离message.context，不隔离这些字段！
```

**并发问题场景**：
```python
# 同时委托给同一个TeamSwarm成员
await asyncio.gather(
    spawn_subagent('worker', 'Task A'),  # 修改worker.loop_step = 1
    spawn_subagent('worker', 'Task B')   # 也修改worker.loop_step = 1，互相干扰
)
```

**解决方案**：`_clone_agent_instance()`方法（已在§3.2.1实现）

**设计原则**：
1. **Per-spawn克隆**：每次spawn创建新实例，独立状态
2. **共享不可变配置**：conf, tool_names, mcp_servers等复制
3. **不共享可变状态**：trajectory, loop_step, _finished等重置
4. **Sandbox可共享**：Sandbox本身无状态，可复用

**性能考虑**：
- 克隆开销：~1ms（Agent初始化）
- 对比收益：避免并发bug >> 克隆开销
- 优化空间：未来可考虑对象池（Phase 2+）

#### 3.2.4 统一工具访问控制（安全修复）

**问题根源**（Codex Adversarial Review）：

设计文档声称工具访问控制是核心目标（最小权限原则），但实现不一致：

```python
# ❌ 旧实现：TeamSwarm成员绕过过滤
if info.source == 'team_member':
    result = await info.agent_instance.async_run(...)  # 使用原始tool_names
else:
    temp_agent = self._create_temp_agent(...)  # 应用_filter_tools()
```

**安全隐患**：
- Parent agent可以委托给拥有broader tool access的TeamSwarm成员
- 违反了最小权限原则（Principle of Least Privilege）
- Trust boundary gap：agent.md子agent受限，TeamSwarm成员不受限

**解决方案**：统一应用`_filter_tools()`（已在§3.2.1 spawn()实现）

```python
# ✅ 新实现：两种来源都过滤
if info.source == 'team_member':
    cloned_agent = self._clone_agent_instance(
        info.agent_instance,
        filtered_tools=self._filter_tools(  # ✅ 应用过滤
            parent_tools=self.agent.tool_names,
            subagent_tools=info.tools,
            disallowed=kwargs.get('disallowedTools', [])
        )
    )
```

**一致性保证**：
- 所有subagent（TeamSwarm + agent.md）都经过`_filter_tools()`
- 工具白名单：subagent只能用parent的工具子集
- 工具黑名单：显式禁止危险工具（terminal, write_file等）
- 审计日志：记录过滤前后的工具列表差异

#### 3.2.5 Trajectory合并策略（Meta-Learning支持）

**设计目标**：支持aworld现有的meta-learning能力，完整记录执行路径。

**问题背景**：

原始aworld设计中，agent执行过程会生成trajectory：
```python
# aworld/core/agent/base.py:165
self.trajectory: List[Tuple[INPUT, Dict[str, Any], AgentResult]] = []
```

Trajectory用途：
1. **Meta-learning**：分析哪些策略有效，学习优化决策
2. **Reflection**：回顾任务执行过程，总结经验
3. **Debugging**：追踪执行路径，定位问题

**Subagent挑战**：

Per-spawn克隆机制（§3.2.3）中，subagent从空trajectory开始：
```python
cloned = original.__class__(...)  # trajectory = []
```

如果不处理，subagent的执行路径会丢失，无法支持meta-learning。

**策略选择**：

| 策略 | 描述 | 优点 | 缺点 | 实施阶段 |
|------|------|------|------|----------|
| A. 合并到parent | Subagent trajectory合并到parent | 简单，兼容现有代码 | Parent trajectory变大 | ✅ Phase 1 |
| B. 独立记录 | 存储在working_state | 细粒度分析 | 需要额外查询接口 | Phase 2+ |
| C. 分层结构 | 树形trajectory | 支持递归subagent | 复杂度高 | Phase 2+ |

**Phase 1实现（策略A）**：

```python
# spawn()方法最后
if hasattr(subagent_agent, 'trajectory') and subagent_agent.trajectory:
    parent_trajectory_item = (
        directive,  # subagent输入
        {
            'subagent_name': name,
            'subagent_source': info.source,
            'tools_used': info.tools,
            'elapsed': elapsed,
            'tokens': sub_context.token_usage
        },
        AgentResult(
            current_state=result.payload,
            actions=subagent_agent.trajectory,  # 🆕 嵌套subagent actions
            is_call_tool=True
        )
    )
    self.agent.trajectory.append(parent_trajectory_item)
```

**数据结构**：

```python
# Parent agent trajectory
[
    # 普通tool调用
    (input1, {tool: 'search'}, AgentResult(...)),
    
    # Subagent调用（包含嵌套trajectory）
    (
        "Analyze data",  # directive
        {
            'subagent_name': 'analyst',
            'subagent_source': 'team_member',
            'tools_used': ['pandas_tool', 'plot_tool'],
            'elapsed': 3.2,
            'tokens': 1500
        },
        AgentResult(
            current_state="Analysis complete",
            actions=[  # 嵌套的subagent trajectory
                (input_a, {tool: 'pandas_tool'}, AgentResult(...)),
                (input_b, {tool: 'plot_tool'}, AgentResult(...))
            ],
            is_call_tool=True
        )
    ),
    
    # 后续操作
    (input3, {tool: 'report_tool'}, AgentResult(...))
]
```

**Meta-Learning应用**：

```python
# 分析parent trajectory，识别subagent调用
def analyze_subagent_effectiveness(trajectory):
    for input, metadata, result in trajectory:
        if 'subagent_name' in metadata:
            subagent_name = metadata['subagent_name']
            subagent_actions = result.actions  # 嵌套trajectory
            
            # 分析subagent的执行效率
            success = evaluate_success(result.current_state)
            elapsed = metadata['elapsed']
            tokens = metadata['tokens']
            
            # 学习：哪些subagent适合哪些任务类型
            learning_db.record(
                task_type=classify_task(input),
                subagent=subagent_name,
                success=success,
                efficiency=(success / (elapsed * tokens))
            )
```

**Phase 2+扩展**：

如果需要更细粒度的分析，可以实施策略B或C：

```python
# 策略B：独立记录
class TaskWorkingState:
    subagent_trajectories: Dict[str, List[Trajectory]] = {}
    
# 策略C：分层结构
class SubagentTrajectoryItem:
    subagent_name: str
    directive: str
    subagent_trajectory: List[...]  # 递归
    result: Any
```

**性能考虑**：

- Trajectory合并开销：~O(1)（append操作）
- 内存增长：与subagent调用次数成正比
- 清理策略：定期清理旧trajectory（可选，Phase 2+）

**测试要求**（Phase 1）：

1. 单元测试：验证trajectory正确合并
2. 集成测试：验证嵌套的actions可访问
3. Meta-learning测试：验证现有分析代码兼容新格式

#### 3.2.6 Spawn Subagent Tool

```python
# aworld/tools/subagent_tool.py

@be_tool(
    tool_name='spawn_subagent',
    tool_desc="""Delegate a subtask to a specialized subagent.

The subagent will execute the task with its own tools and return the result.
Check your system prompt for "Available Subagents" section."""
)
async def spawn_subagent_tool(
    name: str = Field(description="Name of the subagent (see Available Subagents)"),
    directive: str = Field(description="Clear task directive for the subagent"),
) -> str:
    """Tool implementation: delegates to current agent's SubagentManager"""
    from aworld.core.agent.base import BaseAgent
    
    context = BaseAgent._get_current_context()
    if not context:
        return "Error: No active context"
    
    current_agent = context.get("current_agent")
    if not current_agent or not hasattr(current_agent, 'subagent_manager'):
        return "Error: Subagent not enabled"
    
    try:
        result = await current_agent.subagent_manager.spawn(name, directive)
        return f"✅ Subagent '{name}' completed:\n\n{result}"
    except Exception as e:
        return f"❌ Error calling subagent '{name}': {str(e)}"
```

### 3.3 执行流程

```
1. 用户任务到达Parent Agent
   ↓
2. Parent Agent的system_prompt包含可用subagent列表
   （由SubagentManager自动生成）
   ↓
3. LLM理解任务，决策调用subagent
   ↓
4. LLM调用tool: spawn_subagent(name='analyst', directive='...')
   ↓
5. spawn_subagent_tool → SubagentManager.spawn()
   ↓
6. SubagentManager执行：
   a. context.build_sub_context() ✅ 创建独立上下文
   b. 创建/获取subagent实例
   c. subagent.async_run() 执行任务
   d. context.merge_sub_context() ✅ 合并结果
   ↓
7. 结果返回给Parent Agent的LLM
   ↓
8. LLM继续处理或返回给用户
```

---

## 4. 使用场景示例

### 4.1 TeamSwarm自动注册

```python
# 创建TeamSwarm
leader = Agent(
    name='leader',
    system_prompt="You coordinate the team.",
    enable_subagent=True  # 启用subagent
)

researcher = Agent(
    name='researcher',
    system_prompt="You search the web.",
    tool_names=['search', 'web_browser']
)

analyst = Agent(
    name='analyst',
    system_prompt="You analyze data.",
    tool_names=['pandas_tool', 'plot_tool']
)

swarm = TeamSwarm(leader, researcher, analyst)

# 运行时，leader的system_prompt自动包含：
"""
You coordinate the team.

## Available Subagents

You can delegate subtasks using the spawn_subagent tool:

- **researcher**: You search the web.
  - Tools: search, web_browser
  - Usage: spawn_subagent(name='researcher', directive='...')

- **analyst**: You analyze data.
  - Tools: pandas_tool, plot_tool
  - Usage: spawn_subagent(name='analyst', directive='...')
"""

# 用户查询
result = await Runners.run(
    input="分析BABA公司的最新财报",
    swarm=swarm
)

# LLM自主决策：
# 1. 调用 spawn_subagent('researcher', '搜索BABA财报')
# 2. 调用 spawn_subagent('analyst', '分析财报数据')
# 3. 综合结果返回
```

### 4.2 动态扩展（agent.md）

```python
# 用户创建 agents/code-reviewer.md
---
name: code-reviewer
description: Reviews code for best practices
tool_names: [read_file, grep, git_diff]
disallowedTools: [terminal, write_file]  # 安全限制
model: inherit
---

# Code Reviewer

You review code for:
- Security vulnerabilities
- Performance issues
- Style violations

# 通用Agent
agent = Agent(
    name='assistant',
    system_prompt="You help with various tasks.",
    enable_subagent=True,
    subagent_search_paths=['./agents']  # 扫描agent.md
)

# System prompt自动包含从agent.md扫描的subagent
"""
## Available Subagents

- **code-reviewer**: Reviews code for best practices
  - Tools: read_file, grep, git_diff
  - Usage: spawn_subagent(name='code-reviewer', directive='...')
"""

# LLM可以自主调用
# User: "Review this PR"
# Agent: spawn_subagent('code-reviewer', 'Review PR #123')
```

### 4.3 GAIA Agent优化

```python
# Before: 单个agent处理所有能力（context爆炸）
class GAIAAgent(Agent):
    def __init__(self):
        super().__init__(
            tool_names=['search', 'calculator', 'web_browser', 'python_repl', ...]
        )

# After: 按需委托给专家subagent
leader = Agent(
    name='gaia_leader',
    system_prompt="You coordinate specialized experts.",
    enable_subagent=True,
    subagent_search_paths=['./experts']
)

# experts/web-expert.md, experts/math-expert.md, experts/code-expert.md

swarm = TeamSwarm(leader, ...)

# LLM根据任务类型自主选择专家
# Question: "Calculate the square root of 2048"
# Leader: spawn_subagent('math-expert', 'Calculate sqrt(2048)')

# Question: "Find the population of Tokyo"
# Leader: spawn_subagent('web-expert', 'Search Tokyo population')
```

---

## 5. 并发安全性设计

### 5.1 并发场景分析

**场景1：并发spawn调用**
```python
# 同一个agent同时委托多个subagent
results = await asyncio.gather(
    spawn_subagent('researcher', 'Search task A'),
    spawn_subagent('analyst', 'Analyze task B'),
    spawn_subagent('coder', 'Write code for task C')
)
```

**场景2：并发注册**
```python
# 多个agent实例同时注册team members
await asyncio.gather(
    agent1.async_run(message1),  # 触发register_team_members
    agent2.async_run(message2)   # 触发register_team_members
)
```

### 5.2 隔离机制

#### 5.2.1 Context隔离（✅ 已有机制）

**Contextvars机制**（`aworld/core/agent/base.py:28`）：
```python
_agent_context: contextvars.ContextVar[Optional['Context']] = \
    contextvars.ContextVar('_agent_context', default=None)

# 每个async task有独立的context
# spawn()中的sub_context不会污染父context
```

**效果**：
- ✅ 多个spawn并发调用时，每个sub_context独立
- ✅ kv_store deep copy（`amni/__init__.py:819`）保证修改不互相影响
- ✅ merge_sub_context()合并时只更新父context

#### 5.2.2 Registry隔离（🆕 新增机制）

**问题**：`_available_subagents`字典在多线程/并发注册时不安全

**解决方案**：
```python
class SubagentManager:
    def __init__(self):
        self._registry_lock = asyncio.Lock()  # 保护注册操作
        self._registered = False              # 避免重复注册
    
    async def register_team_members(self, swarm: Swarm):
        async with self._registry_lock:
            if self._registered:  # 幂等性
                return
            # ... 注册逻辑
            self._registered = True
```

**效果**：
- ✅ 同一agent只注册一次（幂等性）
- ✅ 多个agent并发注册不会race condition
- ✅ 读取操作（spawn/generate_system_prompt）无锁（快照读取）

### 5.3 数据结构访问模式

| 操作 | 线程安全 | 机制 |
|------|---------|------|
| `register_team_members()` | ✅ | `asyncio.Lock` |
| `scan_agent_md_files()` | ✅ | `asyncio.Lock` |
| `spawn()` 读取subagent | ✅ | 快照读取（dict不在遍历时修改） |
| `generate_system_prompt_section()` | ✅ | 快照读取 |
| `build_sub_context()` | ✅ | Contextvars隔离 |
| `merge_sub_context()` | ✅ | Context自身保证原子性 |

### 5.4 并发性能优化

#### 5.4.1 读写分离

**设计原则**：
- **写操作**（注册）：低频，使用锁保护
- **读操作**（spawn、生成prompt）：高频，无锁快照读取

**实现**：
```python
# 快照读取避免锁竞争
def generate_system_prompt_section(self):
    subagents_snapshot = dict(self._available_subagents)  # 浅拷贝
    # 使用snapshot，不阻塞写操作
```

#### 5.4.2 注册幂等性

**设计目标**：避免每次async_run都尝试注册（性能浪费）

**实现**：
```python
async def async_run(self, message: Message, **kwargs):
    if swarm and not self.subagent_manager._registered:  # 检查标志
        await self.subagent_manager.register_team_members(swarm)
```

**效果**：
- 首次运行：注册team members（1次锁操作）
- 后续运行：跳过注册（0次锁操作）

### 5.5 并发测试策略

#### 5.5.1 单元测试

```python
# tests/test_subagent_concurrency.py

async def test_concurrent_spawn():
    """测试并发spawn调用"""
    manager = SubagentManager(agent)
    
    # 10个并发spawn
    tasks = [
        manager.spawn(f'agent_{i % 3}', f'Task {i}')
        for i in range(10)
    ]
    
    results = await asyncio.gather(*tasks)
    
    # 验证：所有调用成功
    assert len(results) == 10
    # 验证：context正确隔离
    # 验证：token正确累加

async def test_concurrent_registration():
    """测试并发注册"""
    # 多个agent实例并发注册
    agents = [Agent(name=f'agent{i}', enable_subagent=True) for i in range(5)]
    swarm = TeamSwarm(*agents)
    
    # 并发触发async_run（会触发注册）
    tasks = [agent.async_run(message) for agent in agents]
    
    await asyncio.gather(*tasks)
    
    # 验证：每个agent只注册一次
    for agent in agents:
        assert agent.subagent_manager._registered
```

#### 5.5.2 集成测试

```python
# tests/integration/test_teamswarm_concurrent_subagent.py

async def test_leader_concurrent_delegation():
    """测试Leader并发委托给多个Subagent"""
    leader = Agent(name='leader', enable_subagent=True)
    workers = [Agent(name=f'worker{i}', tool_names=[f'tool{i}']) for i in range(5)]
    
    swarm = TeamSwarm(leader, *workers)
    
    # 模拟LLM并发调用spawn_subagent
    result = await Runners.run(
        input="Parallel task: fetch data, analyze, plot, report, notify",
        swarm=swarm
    )
    
    # 验证：sub_task_list包含所有worker的调用
    assert len(result.context.sub_task_list) >= 5
```

### 5.6 潜在并发问题与缓解

#### 问题1：Prompt生成与注册竞争

**场景**：
```python
# Thread A: 生成system prompt（读取_available_subagents）
# Thread B: 扫描agent.md（写入_available_subagents）
```

**风险**：Python字典在并发读写时可能raise RuntimeError

**缓解**：
1. ✅ 快照读取（`dict(self._available_subagents)`）
2. ✅ agent.md扫描在初始化时完成（async_run前）
3. ✅ 注册只在首次async_run时执行（后续跳过）

#### 问题2：Context合并顺序依赖

**场景**：
```python
# Spawn A和B并发执行
# 都调用context.merge_sub_context()
# 是否有顺序依赖？
```

**分析**：
- `merge_sub_context()`更新kv_store：字典update操作（原子性）
- 更新token_usage：简单加法（原子性）
- 更新sub_task_list：查找并更新（可能需要保护）

**缓解**：
- ✅ Context层已有机制保证merge原子性（需验证）
- 🔍 Phase 1测试：验证并发merge的正确性

---

## 6. 技术决策

### 6.1 为什么不用Docker容器隔离？

**决策**：Subagent使用逻辑隔离（contextvars + TaskWorkingState），不使用Docker容器。

**理由**：
1. Subagent隔离的是**上下文**（防止污染），不是**代码执行**
2. 工具执行的安全隔离属于Sandbox层（未来可加）
3. Docker开销大，与Subagent"轻量级调用"理念冲突
4. 逻辑隔离已足够：独立working_state + 工具白名单/黑名单

### 6.2 为什么不直接用Runners.run()？

**对比**：

| 维度 | Runners.run() | spawn_subagent() |
|------|--------------|------------------|
| 上下文 | 全新context | 继承父context（workspace/config） |
| Session | 独立session_id | 共享父session_id |
| 结果合并 | 手动处理 | 自动合并（kv_store/token/sub_task_list） |
| 工具权限 | 完整权限 | 受限（白名单+黑名单） |
| 调用方式 | 编程调用 | LLM Tool调用 |

**适用场景**：
- **Runners.run()**：完全独立的任务（新报告生成、独立评估）
- **spawn_subagent()**：子任务委托（需共享workspace/kv_store，LLM自主决策）

### 6.3 为什么不强绑定agent.md？

**决策**：支持两种subagent来源（TeamSwarm成员 + agent.md）。

**理由**：
1. TeamSwarm场景：成员已预先创建，无需重复加载
2. 性能考虑：实例复用 > 动态创建
3. 灵活性：用户可选择硬编码（TeamSwarm）或动态扩展（agent.md）

### 6.4 工具过滤策略

```python
def _filter_tools(parent_tools, subagent_tools, disallowed):
    # 1. 白名单逻辑
    if subagent_tools == ['*']:
        allowed = parent_tools  # 继承父agent所有工具
    else:
        allowed = [t for t in subagent_tools if t in parent_tools]  # 交集
    
    # 2. 黑名单过滤
    return [t for t in allowed if t not in disallowed]
```

**设计原则**：最小权限原则（Principle of Least Privilege）
- Subagent只能用父agent的工具子集
- 显式禁止危险工具（terminal, write_file等）

---

## 7. 实施计划

### Phase 0：基础设施修复（1-1.5周）🆕

**目标**：修复token merge bug和验证现有上下文机制

**任务**：
1. 修复token重复计数bug（0.5周）
   - 移除`ApplicationContext.merge_sub_context()`中的重复累加
   - 或调整父类`Context.merge_context()`的累加逻辑
   - 代码路径：`aworld/core/context/amni/__init__.py:929-932`

2. 验证修复正确性（0.5周）
   - 单元测试：验证单次merge token正确性
   - 单元测试：验证多次嵌套merge token正确性
   - 集成测试：验证现有subtask场景不受影响
   - 回归测试：运行GAIA/XBench baseline确认无性能回退

3. 文档更新（0.5周，可选）
   - 添加token merge机制说明文档
   - 更新开发者指南（正确使用build_sub_context/merge_sub_context）

**验证**：
- 所有现有使用`merge_sub_context()`的场景通过测试
- Token统计与实际LLM调用token一致（±5%容差）
- GAIA baseline Pass@1 ≥67.89%（确认修复无副作用）

**输出**：
- ✅ Token merge bug修复PR（合并到main）
- ✅ 单元测试覆盖token merge场景
- ✅ Baseline验证报告

**依赖关系**：
- **Phase 1依赖Phase 0完成**（token accounting是subagent性能监控的基础）

### Phase 1：核心机制（4.5周）

**目标**：实现基础spawn_subagent功能（LLM Tool模式）+ 并发安全性

**前置条件**：✅ Phase 0完成（token merge bug修复）

**任务**：
1. 实现SubagentManager（2.5周）
   - register_team_members() with asyncio.Lock
   - scan_agent_md_files() with error handling
   - generate_system_prompt_section() with length limit
   - spawn()核心逻辑 + 审计日志
   - **🆕 _clone_agent_instance()实现（Per-spawn克隆）**
   - **🆕 _filter_tools()统一应用（TeamSwarm + agent.md）**
   - **🆕 Trajectory合并逻辑（策略A：合并到parent）**

2. 实现spawn_subagent_tool（1周）
   - Tool注册
   - 与SubagentManager集成

3. Agent增强（1周）
   - enable_subagent参数
   - _build_system_prompt()动态生成
   - async_run()自动注册team members

4. 并发安全性测试（0.5周）
   - 并发spawn测试（asyncio.gather）
   - **🆕 Agent实例克隆隔离验证（trajectory/loop_step独立性）**
   - 并发注册测试（race condition验证）
   - Context隔离验证（kv_store独立性）

5. 工具访问控制测试（0.5周）🆕
   - 验证TeamSwarm成员工具过滤生效
   - 验证agent.md工具过滤生效
   - 验证白名单逻辑（交集）
   - 验证黑名单逻辑（禁止列表）

**验证**：
- 单元测试：SubagentManager各方法
- 集成测试：TeamSwarm场景 + agent.md场景
- 并发测试：并发spawn、并发注册 🆕
- **🆕 Trajectory测试**：
  - 验证subagent trajectory正确合并到parent
  - 验证嵌套的actions可访问
  - 验证metadata包含正确的subagent信息
  - 验证现有meta-learning代码兼容新格式
- 手动测试：创建简单TeamSwarm，验证LLM能否看到并调用subagent

### Phase 2：高级特性（1周）

**目标**：模型继承 + Prompt优化

**任务**：
1. 模型继承（0.5周）
   - inherit策略实现
   - 模型别名解析（opus/sonnet/haiku）
   - 从父agent继承模型配置
   - 嵌套继承链追溯（subagent of subagent）

2. System Prompt优化（0.5周）
   - 长度监控：记录实际prompt token开销
   - Top-K选择：按调用频率排序subagent列表
   - 分层提示：常用subagent详细描述，其他简略
   - 性能验证：确认prompt overhead ≤5%

**验证**：
- 单元测试：模型继承各种场景（单层/嵌套/循环检测）
- 集成测试：验证模型正确传递
- 性能测试：System prompt token开销测量

### Phase 3：BDD验证（2周）

**目标**：用GAIA/XBench benchmark验证效果 + 性能profiling

**任务**：
1. GAIA Agent重构（1周）
   - 将单体agent改为leader + experts
   - 专家subagent定义（web-expert, math-expert, code-expert）
   - 对比token usage

2. Benchmark运行（1周）
   - 运行GAIA validation split（50 tasks）
   - 记录：Pass@1, token usage, 平均响应时间
   - 与baseline对比

3. 性能profiling（0.5周）🆕
   - Token分布分析（total vs per-request）
   - Max single-request token（验证是否降低）
   - Context window利用率（验证是否优化）
   - System prompt overhead测量（subagent列表的实际开销）

**成功标准**：
- Pass@1 ≥67.89%（不降低）
- Token distribution优化 🆕（不要求total减少）
  - Max single-request token -15~20%（主要优化目标）
  - Context window利用率提升（避免爆炸）
  - System prompt overhead ≤5%（可接受）
- 平均响应时间 ≤1.2x baseline（可接受）

### Phase 4（可选）：Meta-Agent（实验性，4周）

**目标**：动态生成subagent

**任务**：
1. create_and_spawn_subagent工具（2周）
   - LLM生成system prompt
   - 自动推断工具需求
   - 临时agent创建

2. generate_reusable_subagent工具（2周）
   - 生成agent.md文件
   - 保存到agents/目录
   - 自动重新扫描

**验证**：
- 实验性评估：生成的subagent质量
- 用户反馈：是否有实际价值

---

## 8. 风险与缓解

### 8.1 LLM调用准确性

**风险**：LLM可能不调用subagent，或调用错误的subagent

**缓解措施**：
1. System prompt清晰描述每个subagent的能力
2. 提供使用示例（few-shot）
3. 记录调用失败case，迭代prompt
4. 提供fallback：调用失败时，parent agent自己处理

### 8.2 性能开销

**风险**：动态创建subagent（agent.md）可能有性能开销

**缓解措施**：
1. 缓存解析的agent.md配置
2. 优先使用TeamSwarm成员（实例复用）
3. Lazy loading：只在调用时创建
4. 性能监控：记录subagent创建/执行时间

### 8.3 工具访问控制不严格

**风险**：Subagent绕过工具限制，执行危险操作

**缓解措施**：
1. Sandbox层统一拦截（未来增强）
2. 工具过滤在agent创建时强制执行
3. 审计日志：记录所有subagent工具调用
4. 明确文档：哪些工具应被禁止（terminal, write_file等）

### 8.4 上下文泄漏

**风险**：Subagent可以访问父agent的敏感数据（kv_store）

**缓解措施**：
1. 深拷贝kv_store（当前实现已支持）
2. 提供namespace隔离机制（可选）
3. 敏感数据标记：父agent标记不可传递的数据
4. 文档指导：哪些数据适合共享

### 8.5 兼容性

**风险**：现有aworld代码可能受影响

**缓解措施**：
1. enable_subagent默认True，但不影响现有行为
2. spawn_subagent工具需显式注册
3. 单元测试覆盖现有功能
4. 逐步rollout：先在新项目试用

---

## 9. 测试策略

### 9.1 单元测试

```python
# tests/test_subagent_manager.py

class TestSubagentManager:
    def test_register_team_members(self):
        """测试TeamSwarm成员注册"""
        
    def test_scan_agent_md_files(self):
        """测试agent.md扫描"""
        
    def test_generate_system_prompt_section(self):
        """测试system_prompt生成"""
        
    def test_filter_tools_whitelist(self):
        """测试工具白名单"""
        
    def test_filter_tools_blacklist(self):
        """测试工具黑名单"""
        
    def test_model_inherit(self):
        """测试模型继承"""

# tests/test_spawn_subagent.py

class TestSpawnSubagent:
    async def test_spawn_team_member(self):
        """测试调用TeamSwarm成员"""
        
    async def test_spawn_from_agent_md(self):
        """测试从agent.md加载"""
        
    async def test_context_isolation(self):
        """测试上下文隔离"""
        
    async def test_context_merge(self):
        """测试上下文合并"""
        
    async def test_tool_restriction(self):
        """测试工具限制"""
    
    async def test_trajectory_merge(self):
        """测试Trajectory合并到parent（策略A）"""
        # 创建parent agent和subagent
        parent = Agent(name='parent', enable_subagent=True)
        worker = Agent(name='worker', tool_names=['tool1'])
        swarm = TeamSwarm(parent, worker)
        
        # 执行spawn
        manager = parent.subagent_manager
        await manager.register_team_members(swarm)
        
        # Mock subagent执行（生成trajectory）
        result = await manager.spawn('worker', 'Do task')
        
        # 验证：parent trajectory包含subagent item
        assert len(parent.trajectory) > 0
        last_item = parent.trajectory[-1]
        
        # 验证结构：(INPUT, metadata, AgentResult)
        directive, metadata, agent_result = last_item
        assert directive == 'Do task'
        assert metadata['subagent_name'] == 'worker'
        assert 'elapsed' in metadata
        assert 'tokens' in metadata
        
        # 验证嵌套的actions
        assert isinstance(agent_result.actions, list)  # 嵌套的subagent trajectory
    
    async def test_trajectory_nested_access(self):
        """测试嵌套trajectory可访问（meta-learning）"""
        # 执行带trajectory的subagent
        parent = Agent(name='parent', enable_subagent=True)
        # ... 执行spawn
        
        # 模拟meta-learning分析
        def analyze_trajectory(trajectory):
            for input, metadata, result in trajectory:
                if 'subagent_name' in metadata:
                    subagent_actions = result.actions  # 访问嵌套trajectory
                    # 验证可以遍历subagent的每一步
                    for sub_input, sub_meta, sub_result in subagent_actions:
                        assert sub_input is not None
        
        analyze_trajectory(parent.trajectory)
        # 验证：无异常抛出，所有数据可访问
```

### 9.2 集成测试

```python
# tests/integration/test_teamswarm_subagent.py

async def test_leader_delegates_to_researcher():
    """测试Leader委托给Researcher"""
    leader = Agent(name='leader', enable_subagent=True)
    researcher = Agent(name='researcher', tool_names=['search'])
    
    swarm = TeamSwarm(leader, researcher)
    
    result = await Runners.run(
        input="搜索Python教程",
        swarm=swarm
    )
    
    # 验证：sub_task_list包含researcher的调用
    assert len(result.context.sub_task_list) > 0
```

### 9.3 Benchmark测试

```bash
# GAIA benchmark
cd examples/gaia
python run.py --split validation --start 0 --end 50

# 记录：
# - Pass@1 rate
# - Token usage (total/avg)
# - Response time (avg/p95)

# 对比baseline（不用subagent的版本）
```

---

## 10. 文档与示例

### 10.1 用户文档

**新增文档**：
- `docs/subagent-guide.md`：Subagent使用指南
- `docs/subagent-security.md`：工具访问控制最佳实践
- `examples/subagent/`：示例代码

**更新文档**：
- `CLAUDE.md`：添加Subagent机制说明
- `README.md`：更新特性列表

### 10.2 示例代码

```python
# examples/subagent/basic_usage.py
"""基础用法：TeamSwarm + Subagent"""

# examples/subagent/agent_md_extension.py
"""动态扩展：用户添加agent.md"""

# examples/subagent/gaia_optimized.py
"""GAIA Agent优化示例"""

# examples/subagent/tool_control.py
"""工具访问控制示例"""
```

---

## 11. 成功指标

### 11.1 功能完整性

- ✅ LLM能看到可用subagent列表
- ✅ LLM能自主调用spawn_subagent
- ✅ 支持TeamSwarm成员作为subagent
- ✅ 支持agent.md动态加载
- ✅ 工具白名单/黑名单生效
- ✅ 模型继承（inherit）生效
- ✅ 上下文隔离（独立working_state）
- ✅ 上下文合并（kv_store/token/sub_task_list）

### 11.2 性能指标

**GAIA Benchmark**：
- Pass@1 ≥67.89%（不降低）
- Token distribution优化：🆕
  - Max single-request token -15~20%（主要目标）
  - Context window利用率提升
  - Total token可能略增（+5~10%可接受）
- 平均响应时间 ≤1.2x baseline

**XBench Benchmark**：
- Pass@1 ≥51%（不降低）
- Token distribution优化（同上）

### 11.3 用户体验

- 用户添加agent.md后，无需改代码即可使用
- System prompt自动包含subagent列表，LLM理解率>90%
- 错误信息清晰（subagent not found, tool denied等）

---

## 12. 后台执行架构（v2.0）

### 12.1 设计动机

**背景问题**：

原有spawn机制（`spawn`和`spawn_parallel`）都是**阻塞式执行**：

```python
# spawn: 阻塞直到单个subagent完成
result = await spawn('researcher', 'Research quantum computing')
# Orchestrator在此等待，无法做其他工作

# spawn_parallel: 阻塞直到所有subagent完成
results = await spawn_parallel([
    {'name': 'task1', ...},
    {'name': 'task2', ...},
    {'name': 'task3', ...}
])
# Orchestrator等待所有任务完成，无法并行工作
```

**实际需求场景**：

1. **Orchestrator有独立工作**：启动多个长期研究任务后，orchestrator需要继续分析现有数据、规划下一步
2. **任务时长差异大**：一些快速验证（5秒）+ 深度研究（5分钟），不应等待所有任务
3. **选择性等待**：根据情况选择等待特定任务，而非all-or-nothing
4. **早退机制**：竞赛算法场景，第一个完成的任务最优，取消其余任务

**设计目标**：

- **非阻塞spawn**：spawn_background返回task_id后立即返回
- **完整生命周期管理**：spawn → check → wait → cancel
- **灵活等待策略**：wait指定任务、wait any、wait all
- **线程安全**：支持并发spawn_background调用

---

### 12.2 核心架构设计

#### 12.2.1 后台任务注册表

```python
# aworld/core/tool/builtin/spawn_subagent_tool.py

class SpawnSubagentTool(AsyncBaseTool):
    def __init__(self, subagent_manager=None, **kwargs):
        super().__init__(**kwargs)
        self.subagent_manager = subagent_manager
        
        # 后台任务注册表
        self._background_tasks: Dict[str, Dict[str, Any]] = {}
        self._bg_lock = asyncio.Lock()  # 线程安全保护
    
    # 任务注册表结构
    # {
    #     'task_id': {
    #         'task': asyncio.Task,        # 运行中的task对象
    #         'name': str,                 # Subagent名称
    #         'directive': str,            # 任务指令
    #         'start_time': float,         # 启动时间戳
    #         'status': str,               # 'running', 'completed', 'error', 'cancelled'
    #         'result': Optional[str],     # 完成结果
    #         'error': Optional[str]       # 错误信息
    #     }
    # }
```

**设计要点**：

1. **任务对象存储**：保存`asyncio.Task`对象以便查询状态和取消
2. **状态机**：running → completed/error/cancelled
3. **元信息记录**：name, directive, start_time用于审计和监控
4. **线程安全**：所有访问通过`asyncio.Lock`保护

---

#### 12.2.2 spawn_background 实现

```python
async def _spawn_background(self, action_model, **kwargs):
    """启动后台任务（非阻塞）"""
    params = action_model.params
    name = params.get('name')
    directive = params.get('directive')
    task_id = params.get('task_id') or f"bg_{name}_{uuid.uuid4().hex[:8]}"
    
    # 1. 参数验证
    if not name or not directive:
        return error_response("Missing name or directive")
    
    # 2. 检查重复task_id
    async with self._bg_lock:
        if task_id in self._background_tasks:
            return error_response(f"Duplicate task_id: {task_id}")
        
        # 3. 创建后台任务（不await！）
        bg_task = asyncio.create_task(
            self._execute_background_task(
                task_id,
                self.subagent_manager,
                name,
                directive,
                spawn_kwargs
            )
        )
        
        # 4. 注册任务（原子操作）
        self._background_tasks[task_id] = {
            'task': bg_task,
            'name': name,
            'directive': directive,
            'start_time': time.time(),
            'status': 'running',
            'result': None,
            'error': None
        }
    
    # 5. 立即返回（非阻塞！）
    return (
        Observation(content=f"Background task started: {task_id}"),
        1.0,
        False, False,
        {'task_id': task_id, 'action': 'spawn_background'}
    )
```

**关键设计决策**：

1. **asyncio.create_task()**：创建任务但不await，实现非阻塞
2. **原子注册**：在`_bg_lock`保护下注册，避免竞争
3. **立即返回task_id**：orchestrator可继续工作
4. **参数传递**：spawn_kwargs包含model, disallowedTools等参数

---

#### 12.2.3 后台执行器

```python
async def _execute_background_task(
    self,
    task_id: str,
    subagent_manager,
    name: str,
    directive: str,
    spawn_kwargs: dict
):
    """实际执行subagent的worker（在后台运行）"""
    try:
        # 1. 调用SubagentManager.spawn（阻塞在后台task中）
        result = await subagent_manager.spawn(name, directive, **spawn_kwargs)
        
        # 2. 成功：更新状态（原子）
        async with self._bg_lock:
            task_info = self._background_tasks[task_id]
            task_info['status'] = 'completed'
            task_info['result'] = str(result)
    
    except asyncio.CancelledError:
        # 3. 取消：标记cancelled
        async with self._bg_lock:
            task_info = self._background_tasks[task_id]
            task_info['status'] = 'cancelled'
        raise  # 重新抛出，让asyncio正确处理
    
    except Exception as e:
        # 4. 错误：捕获异常
        async with self._bg_lock:
            task_info = self._background_tasks[task_id]
            task_info['status'] = 'error'
            task_info['error'] = str(e)
        
        logger.error(f"Background task failed: {task_id}, error: {e}", exc_info=True)
```

**错误处理策略**：

1. **CancelledError**：正常取消流程，标记cancelled并重新抛出
2. **其他异常**：捕获并存储到error字段，不crash主进程
3. **日志记录**：记录失败信息便于排查

---

#### 12.2.4 check_task 实现

```python
async def _check_task(self, action_model, **kwargs):
    """查询后台任务状态（非阻塞）"""
    params = action_model.params
    task_id = params.get('task_id')
    include_result = params.get('include_result', True)
    
    if task_id == 'all':
        # 返回所有任务摘要
        return await self._get_all_tasks_summary()
    
    # 查询特定任务
    async with self._bg_lock:
        if task_id not in self._background_tasks:
            return error_response(f"Task not found: {task_id}")
        
        task_info = self._background_tasks[task_id]
        status = task_info['status']
        elapsed = time.time() - task_info['start_time']
        
        info = {
            'status': status,
            'elapsed': elapsed,
            'action': 'check_task'
        }
        
        # 包含结果（如果已完成）
        if include_result and status == 'completed':
            info['result'] = task_info['result']
        elif status == 'error':
            info['error'] = task_info['error']
    
    content = self._format_task_status(task_id, info)
    reward = 1.0 if status != 'error' else 0.0
    
    return (Observation(content=content), reward, False, False, info)
```

**使用场景**：

1. **check_task('task1')**：查询特定任务状态
2. **check_task('all')**：获取所有任务概览（total, running, completed, failed）
3. **非阻塞查询**：快速返回当前状态，不等待任务完成

---

#### 12.2.5 wait_task 实现

```python
async def _wait_task(self, action_model, **kwargs):
    """等待后台任务完成（阻塞）"""
    params = action_model.params
    task_ids = params.get('task_ids')
    timeout = params.get('timeout', 300)
    
    # 1. 解析task_ids
    if task_ids == 'all':
        ids_to_wait = list(self._background_tasks.keys())
        wait_mode = asyncio.ALL_COMPLETED
    elif task_ids == 'any':
        ids_to_wait = list(self._background_tasks.keys())
        wait_mode = asyncio.FIRST_COMPLETED
    else:
        ids_to_wait = task_ids.split(',')
        wait_mode = asyncio.ALL_COMPLETED
    
    # 2. 收集运行中的任务
    tasks_to_wait = []
    async with self._bg_lock:
        for task_id in ids_to_wait:
            if task_id in self._background_tasks:
                task_info = self._background_tasks[task_id]
                if task_info['status'] == 'running':
                    tasks_to_wait.append(task_info['task'])
    
    # 3. 等待任务完成（带超时）
    if tasks_to_wait:
        done, pending = await asyncio.wait(
            tasks_to_wait,
            timeout=timeout,
            return_when=wait_mode
        )
        timed_out = len(pending) > 0
    else:
        # 所有任务已完成
        done, pending = [], []
        timed_out = False
    
    # 4. 计算统计信息
    completed = len(done)
    pending_count = len(pending)
    already_completed = len(tasks_to_wait) == 0
    
    # 5. 返回结果
    content = self._format_wait_result(completed, pending_count, timed_out)
    reward = 1.0 if not timed_out else 0.5
    
    return (
        Observation(content=content),
        reward,
        False, False,
        {
            'completed': completed,
            'pending': pending_count,
            'timed_out': timed_out,
            'already_completed': already_completed,
            'action': 'wait_task'
        }
    )
```

**等待策略**：

1. **wait('task1,task2')**：等待特定任务列表
2. **wait('all')**：等待所有后台任务（ALL_COMPLETED）
3. **wait('any')**：等待任意一个完成（FIRST_COMPLETED，竞赛场景）
4. **timeout控制**：防止无限等待，返回timed_out标志

---

#### 12.2.6 cancel_task 实现

```python
async def _cancel_task(self, action_model, **kwargs):
    """取消运行中的后台任务"""
    params = action_model.params
    task_id = params.get('task_id')
    
    if task_id == 'all':
        # 取消所有运行中的任务
        cancelled_count = 0
        async with self._bg_lock:
            for tid, task_info in self._background_tasks.items():
                if task_info['status'] == 'running':
                    task_info['task'].cancel()
                    task_info['status'] = 'cancelled'
                    cancelled_count += 1
        
        content = f"Cancelled {cancelled_count} background tasks"
        return (
            Observation(content=content),
            1.0,
            False, False,
            {'cancelled_count': cancelled_count, 'action': 'cancel_task'}
        )
    else:
        # 取消特定任务
        async with self._bg_lock:
            if task_id not in self._background_tasks:
                return error_response(f"Task not found: {task_id}")
            
            task_info = self._background_tasks[task_id]
            if task_info['status'] == 'running':
                task_info['task'].cancel()
                task_info['status'] = 'cancelled'
                cancelled = True
            else:
                cancelled = False  # 已完成或已取消
        
        content = f"Task {task_id}: {'cancelled' if cancelled else 'cannot cancel (not running)'}"
        return (
            Observation(content=content),
            1.0 if cancelled else 0.0,
            False, False,
            {'cancelled': cancelled, 'action': 'cancel_task'}
        )
```

**取消语义**：

1. **只能取消running状态**：completed/error/cancelled状态无法再取消
2. **异步取消**：调用`task.cancel()`，不等待取消完成
3. **状态更新**：标记status='cancelled'
4. **all支持**：批量取消所有运行中任务（竞赛场景early exit）

---

### 12.3 执行流程对比

#### 12.3.1 阻塞模式（spawn / spawn_parallel）

```
Orchestrator
    │
    ├─ spawn('agent', 'directive')
    │   └─ [BLOCKS] ────────────┐
    │                            ▼
    │                      Agent执行
    │                            │
    │   ┌────────────────────────┘
    │   │ 返回结果
    ├─◄─┘
    │
    └─ 使用结果继续工作
```

**特点**：
- ✅ 简单直接，结果立即可用
- ❌ Orchestrator被阻塞，无法并行工作
- ❌ 任务时长差异导致等待浪费

---

#### 12.3.2 非阻塞模式（spawn_background）

```
Orchestrator
    │
    ├─ spawn_background('agent', 'directive')
    │   └─ 返回task_id ────────┐
    │                           │
    ├─ 继续其他工作              ▼
    │  (分析、规划...)      Agent后台执行
    │                           │
    ├─ check_task(task_id)      │
    │   └─ 获取状态 ◄───────────┤
    │                           │
    ├─ 更多orchestrator工作     │
    │                           │
    ├─ wait_task(task_id)       │
    │   └─ [BLOCKS] ◄───────────┘
    │                  完成
    │
    └─ 获取结果，继续工作
```

**特点**：
- ✅ Orchestrator可继续工作（分析、规划、spawn更多任务）
- ✅ 任务重叠执行（max(T_orchestrator, T_subagent)）
- ✅ 灵活等待策略（选择性等待、早退机制）
- ⚠️ 复杂度更高（需管理task_id）

---

### 12.4 并发安全性

#### 12.4.1 数据竞争保护

**问题**：多个spawn_background并发调用时，`_background_tasks`字典访问不安全

**解决方案**：`asyncio.Lock`保护所有写操作

```python
# 写操作：spawn_background注册任务
async with self._bg_lock:
    self._background_tasks[task_id] = {...}

# 写操作：_execute_background_task更新状态
async with self._bg_lock:
    task_info['status'] = 'completed'

# 读操作：check_task查询状态
async with self._bg_lock:
    task_info = self._background_tasks[task_id]
```

**性能优化**：
- 读操作也加锁（Python dict并发读写不安全）
- 锁粒度小（只保护字典访问，不包含subagent执行）
- 快照读取（check 'all'时）

---

#### 12.4.2 Task对象隔离

**问题**：多个后台任务并发执行时，sub_context是否隔离？

**解决方案**：`build_sub_context()`利用contextvars自动隔离

```python
# 每个spawn_background调用
async def _execute_background_task(...):
    # 1. 创建独立sub_context
    sub_context = await context.build_sub_context(
        sub_task_content=directive,
        sub_task_id=f"{name}_{uuid.uuid4().hex[:6]}"
    )
    # contextvars确保每个asyncio.Task有独立context
    
    # 2. 执行subagent（使用独立context）
    result = await subagent_manager.spawn(name, directive, **kwargs)
    
    # 3. 合并（原子操作）
    context.merge_sub_context(sub_context)
```

**隔离机制**：
- ✅ `contextvars.ContextVar`：每个asyncio Task独立
- ✅ `sub_context` kv_store深拷贝：修改不互相影响
- ✅ `merge_sub_context()`：原子更新父context

---

### 12.5 性能特性

#### 12.5.1 性能公式

**时间复杂度**：

```
Sequential:     Σ(T_i) for all tasks i

Parallel:       max(T_i) + overhead

Background:     max(T_orchestrator, max(T_i))
                └─ 任务重叠！
```

**实际测试结果**（3任务 @ 500ms each）：

| 模式 | Spawn时间 | Total时间 | 加速比 |
|------|----------|----------|--------|
| Sequential | N/A | 1500ms | 1x |
| spawn_parallel | N/A | 500ms + overhead | 3x |
| spawn_background | <10ms | 500ms (with overlap) | 3x |

**Background额外优势**：
- Orchestrator可在500ms期间做100ms工作
- 实际总时间：max(500ms, 100ms) = 500ms
- spawn_parallel总时间：500ms + 100ms = 600ms

---

#### 12.5.2 内存开销

**每个后台任务**：

```python
{
    'task': asyncio.Task,        # ~500 bytes
    'name': str,                 # ~50 bytes
    'directive': str,            # ~100 bytes
    'start_time': float,         # 8 bytes
    'status': str,               # ~20 bytes
    'result': Optional[str],     # 0-10KB
    'error': Optional[str]       # 0-1KB
}
```

**总开销**：~700 bytes/task + result size

对于100个后台任务：~70 KB + results

---

### 12.6 使用模式

#### 12.6.1 Spawn and Forget

```python
# 启动多个后台研究任务
for topic in ['AI', 'Quantum', 'Blockchain']:
    spawn_background(
        name='researcher',
        directive=f'Research {topic}'
    )
    # 不等待，继续spawn下一个

# Orchestrator继续工作，完全不等待
```

---

#### 12.6.2 Spawn, Work, Then Collect

```python
# 1. 启动后台任务
task_ids = []
for topic in ['AI', 'Quantum', 'Blockchain']:
    result = spawn_background(
        name='researcher',
        directive=f'Research {topic}',
        task_id=f'research_{topic}'
    )
    task_ids.append(result['task_id'])

# 2. Orchestrator做其他工作
analyze_previous_data()
plan_next_steps()

# 3. 等待所有研究完成
wait_task(task_ids=','.join(task_ids), timeout=300)

# 4. 获取结果
for task_id in task_ids:
    result = check_task(task_id=task_id, include_result=True)
    process(result['result'])
```

---

#### 12.6.3 竞赛算法（First-Wins）

```python
# 1. 启动多个竞争方案
for approach in ['method_a', 'method_b', 'method_c']:
    spawn_background(
        name='solver',
        directive=f'Solve using {approach}',
        task_id=f'solve_{approach}'
    )

# 2. 等待任意一个完成
wait_task(task_ids='any', timeout=60)

# 3. 取消其余任务
cancel_task(task_id='all')

# 4. 使用第一个完成的结果
for task_id in ['solve_method_a', 'solve_method_b', 'solve_method_c']:
    status = check_task(task_id=task_id)
    if status['status'] == 'completed':
        winner = status['result']
        break
```

---

### 12.7 测试覆盖

#### 12.7.1 单元测试

**文件**：`tests/core/tool/test_spawn_background.py`

**覆盖场景**（18个测试用例）：

1. **spawn_background**：
   - 成功spawn（返回task_id）
   - 自定义task_id
   - 重复task_id检测
   - 缺失参数错误

2. **check_task**：
   - 查询running状态
   - 查询completed状态（包含result）
   - 查询all（摘要）
   - 不存在的task_id

3. **wait_task**：
   - 等待单个任务
   - 等待多个任务
   - 超时处理
   - 已完成任务（立即返回）

4. **cancel_task**：
   - 取消running任务
   - 取消completed任务（失败）
   - 取消all

5. **错误处理**：
   - Subagent执行异常捕获
   - 错误状态传播

---

#### 12.7.2 集成测试

**文件**：`examples/subagent_integration/test_background_spawn.py`

**测试场景**：

1. **Orchestrator后台执行**：
   - 3个并发后台任务
   - 验证任务重叠（非阻塞）
   - 性能验证：3x加速（0.50s vs 1.50s sequential）

2. **混合foreground/background**：
   - 后台启动slow task
   - Foreground执行quick task
   - 验证任务重叠（0.80s vs 1.10s sequential）

---

### 12.8 最佳实践

#### 12.8.1 Task ID管理

```python
# ✅ 推荐：描述性自定义ID
spawn_background(
    name='researcher',
    directive='Research quantum computing',
    task_id='research_quantum_2026'
)

# ❌ 不推荐：依赖自动生成（难以追踪）
spawn_background(
    name='researcher',
    directive='Research quantum computing'
)  # 生成 bg_researcher_a8f3c2
```

---

#### 12.8.2 超时策略

```python
# ✅ 设置合理超时
wait_task(task_ids='task1,task2', timeout=300)  # 5分钟

# ⚠️ 无限等待风险
wait_task(task_ids='task1,task2')  # 默认300s，但应显式指定
```

---

#### 12.8.3 错误处理

```python
# ✅ 总是检查状态
result = check_task(task_id='task1')
if result['status'] == 'error':
    logger.error(f"Task failed: {result['error']}")
    handle_error()
elif result['status'] == 'completed':
    process(result['result'])

# ❌ 假设成功
result = check_task(task_id='task1')
process(result['result'])  # result可能不存在！
```

---

#### 12.8.4 资源限制

```python
# ✅ 限制并发后台任务数
MAX_BACKGROUND = 10

async def safe_spawn_background(name, directive):
    active = count_running_tasks()
    if active >= MAX_BACKGROUND:
        # 等待一些任务完成
        await wait_any_complete()
    
    return spawn_background(name=name, directive=directive)

# ❌ 无限spawn（资源耗尽）
for i in range(1000):
    spawn_background(name='worker', directive=f'Task {i}')
```

---

### 12.9 与其他机制对比

#### 12.9.1 spawn vs spawn_parallel vs spawn_background

| 维度 | spawn | spawn_parallel | spawn_background |
|------|-------|----------------|------------------|
| **阻塞性** | 完全阻塞 | 阻塞直到所有完成 | 非阻塞（立即返回） |
| **Orchestrator工作** | 不能 | 不能 | 可以（任务重叠） |
| **结果收集** | 立即可用 | 自动聚合 | 手动查询 |
| **使用复杂度** | 低 | 中 | 高 |
| **适用场景** | 单任务 | 需所有结果 | Orchestrator有独立工作 |

---

#### 12.9.2 选择决策树

```
需要所有subagent结果才能继续？
├─ 是 → 使用spawn_parallel（简单）
└─ 否 → Orchestrator有独立工作？
    ├─ 是 → 使用spawn_background（性能最优）
    └─ 否 → 使用spawn_parallel（复杂度低）
```

---

### 12.10 未来增强

#### 12.10.1 任务优先级

```python
spawn_background(
    name='urgent_task',
    directive='...',
    priority='high'  # 高优先级先执行
)
```

---

#### 12.10.2 任务依赖

```python
spawn_background(
    name='task2',
    directive='...',
    depends_on=['task1']  # task1完成后才启动
)
```

---

#### 12.10.3 进度回调

```python
spawn_background(
    name='long_task',
    directive='...',
    on_progress=lambda pct: logger.info(f"Progress: {pct}%")
)
```

---

#### 12.10.4 持久化存储

```python
# 保存任务状态，重启后恢复
save_task_state(task_id='task1', persist=True)

# 重启后
resume_task(task_id='task1')
```

---

### 12.11 架构图

```
┌─────────────────────────────────────────────────────────┐
│                 SpawnSubagentTool                        │
│  ┌───────────────────────────────────────────────────┐  │
│  │         Background Task Registry                  │  │
│  │  ┌────────────────────────────────────────────┐  │  │
│  │  │ _background_tasks: Dict[str, TaskInfo]    │  │  │
│  │  │ _bg_lock: asyncio.Lock                    │  │  │
│  │  └────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────┘  │
│                         │                                │
│  ┌──────────────────────┴──────────────────────┐        │
│  │    spawn_background(name, directive)         │        │
│  │      └─> asyncio.create_task(...)           │        │
│  │      └─> 注册到registry                      │        │
│  │      └─> 立即返回task_id                     │        │
│  └──────────────────────────────────────────────┘        │
│                         │                                │
│  ┌──────────────────────┴──────────────────────┐        │
│  │  _execute_background_task (worker)          │        │
│  │    ├─ build_sub_context() (独立context)     │        │
│  │    ├─ subagent_manager.spawn() (阻塞)       │        │
│  │    └─ merge_sub_context() (合并结果)        │        │
│  └──────────────────────────────────────────────┘        │
│                         │                                │
│  ┌──────────────────────┴──────────────────────┐        │
│  │  check_task / wait_task / cancel_task       │        │
│  │    ├─ 查询registry                           │        │
│  │    ├─ asyncio.wait() (等待完成)              │        │
│  │    └─ task.cancel() (取消任务)               │        │
│  └──────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────┘
```

---

## 13. 未来扩展

### 13.1 Background Subagent（✅ 已实现，见§12）

支持异步执行subagent（不阻塞parent agent）：

```python
# 已有支持！
sub_context = await context.build_sub_context(
    sub_task_content=directive,
    task_type='background'  # 异步执行
)

# 可以添加：
await self.spawn_subagent(name='long-running-task', directive='...', background=True)
```

### 12.2 Subagent Hooks

类似Claude Code的PreToolUse/PostToolUse hooks：

```python
# agents/monitored-agent.md
---
name: monitored-agent
hooks:
  pre_tool_use: check_permission
  post_tool_use: log_result
---
```

### 12.3 Subagent Memory Persistence

Subagent调用历史存储，跨会话复用：

```python
# 记录成功的subagent配置
await memory.store_successful_subagent(
    task_type='stock_analysis',
    subagent_name='financial-analyst',
    success_rate=0.95
)

# 下次类似任务，推荐使用
recommended = await memory.recommend_subagent(task='分析股票')
```

### 12.4 Subagent Federation

跨机器/跨服务的subagent调用（Remote Subagent）：

```python
# agents/remote-expert.md
---
name: remote-expert
endpoint: https://expert-service.com/api/v1/invoke
auth: bearer_token
---
```

---

## 13. 参考资料

1. **Claude Code Documentation**
   - Subagents: https://code.claude.com/docs/en/sub-agents
   - Agent Teams: https://code.claude.com/docs/en/teams

2. **AWorld现有机制**
   - `aworld/core/context/amni/__init__.py`: ApplicationContext
   - `build_sub_context()`: Line 752-772
   - `merge_sub_context()`: Line 900-932
   - `markdown_agent_loader.py`: Agent.md解析

3. **相关Issue/PR**
   - #843: Tool logging fix
   - #842: Hybrid improvements
   - #840: Hybrid MAS architecture

---

## 附录A：agent.md格式扩展

```yaml
---
# 基础字段（已有）
name: my-subagent
description: Subagent description
tool_names: [read_file, search]
mcp_servers: [ms-playwright]
model_config: {...}

# 🆕 Subagent扩展字段
disallowedTools: [terminal, write_file]  # 黑名单
model: inherit  # 或 'opus', 'sonnet', 'haiku', 'gpt-4'
maxTurns: 30  # 最大轮次（可选，Phase 2+）
---

# Agent Description

System prompt content...
```

---

## 附录B：System Prompt自动生成示例

```
You are a research coordinator.

## Available Subagents

You can delegate subtasks to specialized subagents using the spawn_subagent tool:

- **web-researcher**: Searches the web for information
  - Tools: search, web_browser
  - Usage: spawn_subagent(name='web-researcher', directive='Search for Python tutorials')

- **data-analyst**: Analyzes data and generates insights
  - Tools: pandas_tool, plot_tool, read_file
  - Usage: spawn_subagent(name='data-analyst', directive='Analyze sales data')

- **code-reviewer**: Reviews code for best practices
  - Tools: read_file, grep, git_diff
  - Usage: spawn_subagent(name='code-reviewer', directive='Review PR #123')

When you need specialized help, call the appropriate subagent. They will return results for you to continue processing.
```

---

## 附录C：关键代码位置

| 组件 | 文件路径 | 说明 |
|------|---------|------|
| SubagentManager | `aworld/core/agent/subagent_manager.py` | 新增 |
| spawn_subagent_tool | `aworld/tools/subagent_tool.py` | 新增 |
| Agent增强 | `aworld/agents/llm_agent.py` | 修改 |
| Context机制 | `aworld/core/context/amni/__init__.py` | 复用 |
| Agent.md解析 | `aworld-cli/src/aworld_cli/core/markdown_agent_loader.py` | 复用 |

---

## 附录D：版本历史

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| 1.0 | 2026-04-07 | 初始设计版本 |
| 1.1 | 2026-04-07 | 补充并发安全性设计（第5章）、错误处理、审计日志、性能profiling |
| 1.2 | 2026-04-07 | **Codex Review修复**：Token重复计数bug（§3.1.1）、Agent实例并发安全（§3.2.3）、工具访问控制统一（§3.2.4）、Phase 0基础设施修复 |
| 1.3 | 2026-04-07 | **Trajectory集成**：策略A实现（§3.2.5）、Trajectory合并逻辑、Meta-learning支持、测试覆盖 |

---

**文档结束**

*本设计文档基于2026-04-07的架构讨论，结合aworld现有机制和Claude Code参考实现，并经过Codex Adversarial Review验证。*

**实施周期总结**：
- **Phase 0**（基础设施修复）：1-1.5周
- **Phase 1-3**（核心功能+验证）：8-9周
- **总计**：**9-10.5周**（含critical bug修复）

**关键修复**（v1.2-1.3）：
- ✅ **Token merge bug修复**（Phase 0）：移除重复累加，修复成本统计
- ✅ **Agent实例克隆**（Phase 1）：Per-spawn克隆，避免并发状态竞争
- ✅ **工具过滤统一**（Phase 1）：TeamSwarm成员和agent.md都应用最小权限
- ✅ **Trajectory合并**（Phase 1）：策略A实现，支持meta-learning
- ✅ **并发安全机制**（Phase 1）：asyncio.Lock + 快照读取
- ✅ **错误处理增强**（Phase 1）：agent.md解析、subagent调用
- ✅ **审计日志**（Phase 1）：spawn开始/成功/失败
- ✅ **System prompt优化**（Phase 2）：长度限制、Top-K选择
- ✅ **性能profiling**（Phase 3）：Token distribution vs Total token

**v1.1更新要点**：
- 并发安全性设计（第5章）
- 错误处理、审计日志
- 性能指标优化

**v1.2更新要点**（Codex Review反馈）：
- 识别并修复token重复计数bug
- 设计Per-spawn agent实例克隆机制
- 统一工具访问控制（两种来源）
- 新增Phase 0基础设施修复
- 调整实施周期：7-13.5周 → 9-10.5周

**v1.3更新要点**（Trajectory集成）：
- ✅ **策略A：合并Subagent Trajectory到Parent**（§3.2.5）
- ✅ **Trajectory合并逻辑**：spawn()完成后自动合并
- ✅ **Meta-learning支持**：嵌套actions可访问，支持现有分析代码
- ✅ **数据结构设计**：(directive, metadata, AgentResult with nested actions)
- ✅ **测试覆盖**：单元测试 + 集成测试 + Meta-learning兼容性测试
- ✅ **Phase 2+扩展路径**：策略B（独立记录）、策略C（分层结构）

**v2.0更新要点**（后台执行机制）：
- ✅ **背景执行模式**：实现非阻塞的Fire-and-Forget模式（§12.1扩展为完整实现）
- ✅ **任务生命周期管理**：spawn_background, check_task, wait_task, cancel_task
- ✅ **任务注册表**：线程安全的后台任务跟踪机制（asyncio.Lock保护）
- ✅ **性能优化**：Orchestrator可在subagent执行期间继续工作（任务重叠）
- ✅ **测试覆盖**：单元测试（18个测试用例） + 集成测试（性能验证3x加速）
- ✅ **文档完整**：功能文档 + 架构设计 + 最佳实践
