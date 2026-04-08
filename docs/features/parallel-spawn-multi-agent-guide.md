# 并行Spawn多个不同Agent完全指南

**重要说明:** `spawn_parallel`完全支持在一次调用中spawn多个不同的subagent!

---

## 核心特性

✅ **每个task可以指定不同的subagent**  
✅ **支持混合agent类型** (TeamSwarm成员 + agent.md加载的agent)  
✅ **每个agent可以有不同的工具配置**  
✅ **每个agent可以使用不同的LLM模型**

---

## 快速示例

### 示例1: 三个完全不同的Agent

```python
coordinator = Agent(
    name="coordinator",
    enable_subagent=True,
    system_prompt="""使用spawn_parallel同时执行多个不同agent:

spawn_subagent(
    action="spawn_parallel",
    tasks=[
        {
            "name": "code_analyzer",      # Agent 1: 代码分析
            "directive": "分析代码质量"
        },
        {
            "name": "doc_writer",         # Agent 2: 文档编写
            "directive": "生成API文档"
        },
        {
            "name": "test_runner",        # Agent 3: 测试执行
            "directive": "运行单元测试"
        }
    ]
)
"""
)

# 创建三个不同的subagent
code_analyzer = Agent(
    name="code_analyzer",
    desc="代码质量分析专家",
    tool_names=["read_file", "CAST_ANALYSIS"]
)

doc_writer = Agent(
    name="doc_writer",
    desc="技术文档编写专家",
    tool_names=["read_file", "write_file"]
)

test_runner = Agent(
    name="test_runner",
    desc="自动化测试专家",
    tool_names=["terminal"]
)

# 组装TeamSwarm
swarm = TeamSwarm(
    coordinator,
    code_analyzer,    # ← 不同的agent
    doc_writer,       # ← 不同的agent
    test_runner       # ← 不同的agent
)
```

**执行结果:**
- `code_analyzer`并发分析代码
- `doc_writer`并发生成文档
- `test_runner`并发执行测试
- 三个agent完全独立,互不干扰

---

### 示例2: 混合使用相同和不同的Agent

```python
tasks=[
    {"name": "data_analyzer", "directive": "分析数据集A"},   # Agent 1
    {"name": "data_analyzer", "directive": "分析数据集B"},   # Agent 1 (复用)
    {"name": "visualizer", "directive": "生成图表"},        # Agent 2 (不同)
    {"name": "reporter", "directive": "生成报告"}           # Agent 3 (不同)
]
```

**关键点:**
- 相同的agent (`data_analyzer`)可以并发执行多个任务
- 每次spawn都是独立的agent实例(无状态冲突)
- 混合使用多种agent类型完全支持

---

### 示例3: 每个Agent使用不同的模型

```python
tasks=[
    {
        "name": "fast_analyzer",
        "directive": "快速初步分析",
        "model": "gpt-4o-mini"      # 使用快速模型
    },
    {
        "name": "deep_analyzer",
        "directive": "深度分析",
        "model": "gpt-4o"           # 使用强大模型
    },
    {
        "name": "code_generator",
        "directive": "生成代码",
        "model": "claude-sonnet-4"  # 使用Claude模型
    }
]
```

**优势:**
- 根据任务复杂度选择合适模型
- 优化成本(简单任务用便宜模型)
- 优化性能(复杂任务用强大模型)

---

### 示例4: 每个Agent有不同的工具权限

```python
tasks=[
    {
        "name": "security_scanner",
        "directive": "扫描安全漏洞",
        "disallowedTools": "write_file,terminal"  # 只读权限
    },
    {
        "name": "code_fixer",
        "directive": "修复发现的问题",
        "disallowedTools": "terminal"  # 可以写文件,不能执行命令
    },
    {
        "name": "test_executor",
        "directive": "运行测试",
        "disallowedTools": ""  # 完整权限(包括terminal)
    }
]
```

**安全最佳实践:**
- 扫描器只读(防止误修改)
- 修复器可写(但不能执行命令)
- 测试器完整权限(需要运行shell命令)

---

## 真实场景示例

### 场景1: 多模块代码审查

```python
# 目标:审查3个不同的模块
swarm = TeamSwarm(
    reviewer,              # 协调者
    python_analyzer,       # Python专家
    javascript_analyzer,   # JavaScript专家
    rust_analyzer         # Rust专家
)

# 使用方式
spawn_subagent(
    action="spawn_parallel",
    tasks=[
        {
            "name": "python_analyzer",
            "directive": "审查backend/api.py模块"
        },
        {
            "name": "javascript_analyzer",
            "directive": "审查frontend/app.js模块"
        },
        {
            "name": "rust_analyzer",
            "directive": "审查core/engine.rs模块"
        }
    ]
)
```

**为什么需要不同agent?**
- 每种语言有专门的工具和知识
- Python分析器:熟悉Python最佳实践
- JavaScript分析器:了解前端安全规范
- Rust分析器:精通所有权和生命周期

---

### 场景2: 多渠道内容生成

```python
swarm = TeamSwarm(
    content_coordinator,
    blog_writer,          # 博客文章专家
    social_media_writer,  # 社交媒体专家
    email_writer         # 邮件营销专家
)

tasks=[
    {
        "name": "blog_writer",
        "directive": "写一篇1500字的技术博客"
    },
    {
        "name": "social_media_writer",
        "directive": "写10条Twitter推文"
    },
    {
        "name": "email_writer",
        "directive": "写一封产品更新邮件"
    }
]
```

**不同agent的价值:**
- 每种内容有不同的风格和长度要求
- 博客:长篇技术深度
- 社交媒体:短小精悍,吸引眼球
- 邮件:正式专业,清晰简洁

---

### 场景3: 多数据源信息聚合

```python
swarm = TeamSwarm(
    aggregator,
    web_searcher,     # 网页搜索专家
    database_querier, # 数据库查询专家
    api_caller       # API调用专家
)

tasks=[
    {
        "name": "web_searcher",
        "directive": "搜索最新的行业报告",
        "disallowedTools": "terminal,write_file"
    },
    {
        "name": "database_querier",
        "directive": "查询内部销售数据",
        "disallowedTools": "terminal"
    },
    {
        "name": "api_caller",
        "directive": "获取第三方市场数据",
        "disallowedTools": "terminal,write_file"
    }
]
```

**不同数据源需要不同agent:**
- Web搜索:需要browser工具
- 数据库:需要SQL工具
- API:需要HTTP请求工具

---

## 技术实现细节

### Agent实例隔离

每次spawn都会创建独立的agent实例:

```python
# SubagentManager._clone_agent_instance()
cloned_agent = original_agent.__class__(
    name=original_agent.name(),
    conf=original_agent.conf.copy(),  # 独立配置
    tool_names=filtered_tools,        # 独立工具
    ...
)
```

**保证:**
- ✅ 无状态冲突(每个实例独立)
- ✅ 工具隔离(可以有不同的工具集)
- ✅ 配置独立(可以覆盖模型等参数)

---

### 并发执行机制

```python
# 并发执行不同agent的任务
async def spawn_with_limit(task):
    name = task['name']  # ← 可以是任意subagent名称
    
    # 根据名称查找对应的subagent
    subagent_info = subagent_manager._available_subagents[name]
    
    # 创建独立实例并执行
    cloned_agent = _clone_agent_instance(subagent_info.agent_instance)
    result = await subagent_manager.spawn(name, directive, **kwargs)
    
    return result

# asyncio.gather并发执行所有任务
results = await asyncio.gather(*[spawn_with_limit(t) for t in tasks])
```

**关键特性:**
- 每个任务独立查找对应的subagent
- 支持混合不同类型的subagent
- 并发执行互不干扰

---

## 常见问题

### Q: 可以同时spawn多少个不同的agent?

**A:** 无硬性限制,受以下因素影响:
- `max_concurrent`参数(默认10)
- 系统资源(内存/CPU)
- API速率限制

**推荐:**
- 5-10个不同agent:安全范围
- 10-20个:需要增加`max_concurrent`
- 20+:考虑分批处理

---

### Q: 相同名称的agent可以并发执行多个任务吗?

**A:** 完全可以!每次spawn都是独立实例:

```python
tasks=[
    {"name": "analyzer", "directive": "任务1"},  # 实例1
    {"name": "analyzer", "directive": "任务2"},  # 实例2
    {"name": "analyzer", "directive": "任务3"}   # 实例3
]
# 三个独立的analyzer实例并发执行
```

---

### Q: 不同agent可以有不同的工具吗?

**A:** 是的!在TeamSwarm中定义时配置:

```python
agent1 = Agent(name="agent1", tool_names=["tool_a", "tool_b"])
agent2 = Agent(name="agent2", tool_names=["tool_c", "tool_d"])
agent3 = Agent(name="agent3", tool_names=["tool_e"])

swarm = TeamSwarm(coordinator, agent1, agent2, agent3)
```

每个agent保持自己的工具配置。

---

### Q: 可以混合TeamSwarm成员和agent.md加载的agent吗?

**A:** 完全支持!

```python
# TeamSwarm成员
team_member = Agent(name="team_agent", ...)

# agent.md文件
# File: .claude/agents/external_agent.md
# ---
# name: external_agent
# description: 外部agent
# tool_names: [tool_x, tool_y]
# ---

# 同时使用
tasks=[
    {"name": "team_agent", "directive": "..."},      # TeamSwarm成员
    {"name": "external_agent", "directive": "..."}   # agent.md加载
]
```

---

## 性能对比

### 顺序执行 vs 并行执行(不同agent)

**场景:** 3个不同的任务,每个5秒

```python
# ❌ 顺序执行(原始方式)
result1 = spawn_subagent(name="agent1", directive="任务1")  # 5s
result2 = spawn_subagent(name="agent2", directive="任务2")  # 5s
result3 = spawn_subagent(name="agent3", directive="任务3")  # 5s
# 总时间: 15秒

# ✅ 并行执行(新方式)
results = spawn_subagent(
    action="spawn_parallel",
    tasks=[
        {"name": "agent1", "directive": "任务1"},
        {"name": "agent2", "directive": "任务2"},
        {"name": "agent3", "directive": "任务3"}
    ]
)
# 总时间: 5秒 (3x加速!)
```

---

## 最佳实践总结

### ✅ 应该使用不同agent的场景

1. **专业化任务**
   - 不同编程语言的分析
   - 不同类型的内容生成
   - 不同数据源的查询

2. **工具隔离**
   - 读操作 vs 写操作
   - 安全扫描 vs 修复操作
   - 本地处理 vs 远程API调用

3. **模型优化**
   - 简单任务用轻量模型
   - 复杂任务用强大模型
   - 成本敏感任务用便宜模型

### ❌ 不适合的场景

1. **有依赖关系的任务**
   ```python
   # ❌ 错误:任务2依赖任务1的结果
   tasks=[
       {"name": "agent1", "directive": "生成数据"},
       {"name": "agent2", "directive": "处理上一步的数据"}
   ]
   # ✅ 正确:使用顺序spawn
   ```

2. **需要共享状态的任务**
   - 每个spawn是独立实例,无法共享内存状态
   - 如需共享,使用文件系统或context.kv_store

---

## 完整工作示例

```python
import asyncio
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import TeamSwarm
from aworld.config.conf import AgentConfig
from aworld.runner import Runners

async def main():
    # 配置
    conf = AgentConfig(llm_provider="openai", llm_model_name="gpt-4o")
    
    # 协调器
    coordinator = Agent(
        name="project_manager",
        conf=conf,
        enable_subagent=True,
        system_prompt="""项目管理agent,负责协调多个专家agent。

当接到代码审查任务时,并行调用三个专家:
spawn_subagent(
    action="spawn_parallel",
    tasks=[
        {"name": "backend_expert", "directive": "审查backend代码"},
        {"name": "frontend_expert", "directive": "审查frontend代码"},
        {"name": "security_expert", "directive": "进行安全扫描"}
    ]
)
"""
    )
    
    # 三个不同的专家agent
    backend_expert = Agent(
        name="backend_expert",
        conf=conf,
        desc="后端代码专家",
        tool_names=["read_file", "CAST_ANALYSIS"]
    )
    
    frontend_expert = Agent(
        name="frontend_expert",
        conf=conf,
        desc="前端代码专家",
        tool_names=["read_file", "grep"]
    )
    
    security_expert = Agent(
        name="security_expert",
        conf=conf,
        desc="安全审计专家",
        tool_names=["read_file", "grep", "glob"]
    )
    
    # 组装TeamSwarm
    swarm = TeamSwarm(
        coordinator,
        backend_expert,
        frontend_expert,
        security_expert
    )
    
    # 执行
    result = await Runners.async_run(
        input="全面审查项目代码",
        swarm=swarm
    )
    
    print(result)

if __name__ == '__main__':
    asyncio.run(main())
```

---

## 总结

**核心要点:**
1. ✅ `spawn_parallel`完全支持多个不同agent
2. ✅ 每个task可以指定不同的subagent name
3. ✅ 支持混合使用相同和不同的agent
4. ✅ 每个agent可以有独立的工具、模型、权限配置
5. ✅ 并发执行时agent实例完全隔离

**使用建议:**
- 根据任务特性选择合适的专家agent
- 利用工具隔离提升安全性
- 根据复杂度选择不同的模型优化成本
- 合理设置`max_concurrent`避免资源耗尽

**下一步:**
查看 [parallel_spawn_example.py](../../examples/subagent_integration/parallel_spawn_example.py) 获取更多代码示例。
