# 并行执行与上下文隔离设计

## 需求背景

### 用户场景

Parent Aworld处理用户请求A时，需要并行处理独立的子任务B，确保：
1. **任务B不干扰A的上下文**（状态、内存、trajectory）
2. **任务B可以独立执行**（有自己的工具集、配置）
3. **任务B完成后结果可以合并回A**（可选）

### 当前限制

**SubagentManager的设计目标不是并行执行：**
- Subagent用于**专业能力委派**（developer, evaluator等）
- Subagent执行是**阻塞式**的（spawn()等待结果）
- 没有任务队列、调度、超时控制等并行执行特性

**为什么不能用"Aworld作为自己的subagent"：**
```python
# ❌ 错误方案：自我委派
spawn(name='aworld', directive='Process task B')

问题：
1. 循环委派风险（Aworld → Aworld → Aworld → ...）
2. 角色混淆（协调者 vs 执行者）
3. 没有并行语义（spawn是阻塞式的，不是并行）
4. 工具集重复（父Aworld和子Aworld有相同工具，没有隔离）
```

## 设计方案

### 架构：ParallelExecutor（并行执行器）

```
┌─────────────────────────────────────────────────────────────┐
│                      Parent Aworld                           │
│  处理用户请求A（主线任务）                                      │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
        ┌────────────────┐
        │ ParallelExecutor│ ← 新组件
        └────────┬───────┘
                 │
        ┌────────┴────────┐
        ▼                 ▼
┌───────────────┐  ┌───────────────┐
│ Task B        │  │ Task C        │
│ (IsolatedCtx) │  │ (IsolatedCtx) │
│               │  │               │
│ Tools: [...]  │  │ Tools: [...]  │
│ Memory: B     │  │ Memory: C     │
└───────────────┘  └───────────────┘
```

### 核心组件

#### 1. ParallelExecutor（并行执行器）

**职责：** 管理多个独立任务的并行执行

```python
class ParallelExecutor:
    """
    并行任务执行器
    
    Features:
    - 任务队列和调度
    - 上下文隔离（每个任务独立Context）
    - 超时控制和错误处理
    - 结果收集和合并
    """
    
    async def submit_task(
        self,
        task_id: str,
        directive: str,
        agent_config: Dict[str, Any] = None,
        context_config: Dict[str, Any] = None,
        timeout: float = None
    ) -> TaskHandle:
        """
        提交一个并行任务
        
        Args:
            task_id: 任务唯一标识
            directive: 任务指令
            agent_config: Agent配置（tools, model等）
            context_config: Context配置（memory, history等）
            timeout: 超时时间（秒）
        
        Returns:
            TaskHandle: 任务句柄，用于查询状态和结果
        """
        pass
    
    async def wait_task(self, task_id: str, timeout: float = None) -> TaskResult:
        """等待任务完成并返回结果"""
        pass
    
    async def wait_all(self, timeout: float = None) -> List[TaskResult]:
        """等待所有任务完成"""
        pass
    
    async def cancel_task(self, task_id: str):
        """取消任务"""
        pass
    
    def get_status(self, task_id: str) -> TaskStatus:
        """获取任务状态（PENDING, RUNNING, COMPLETED, FAILED, CANCELLED）"""
        pass
```

#### 2. IsolatedContext（隔离上下文）

**职责：** 为每个并行任务创建独立的执行上下文

```python
class IsolatedContext(AmniContext):
    """
    隔离的执行上下文
    
    Features:
    - 独立的memory（不影响parent context）
    - 独立的trajectory（可选：合并回parent）
    - 独立的工具集（可以和parent不同）
    - 独立的token tracking（防止相互干扰）
    """
    
    def __init__(
        self,
        parent_context: Context,
        task_id: str,
        config: IsolatedContextConfig
    ):
        """
        从parent context派生，但完全独立运行
        
        Args:
            parent_context: 父上下文（提供基础配置）
            task_id: 任务ID
            config: 隔离配置（哪些共享，哪些独立）
        """
        super().__init__(...)
        self.parent_context = parent_context
        self.task_id = task_id
        self.isolation_config = config
    
    async def merge_back(self, merge_config: MergeConfig):
        """
        将结果合并回parent context（可选操作）
        
        Args:
            merge_config: 控制哪些内容合并
                - merge_memory: 是否合并memory
                - merge_trajectory: 是否合并trajectory
                - merge_outputs: 是否合并输出结果
        """
        pass
```

#### 3. TaskHandle（任务句柄）

```python
class TaskHandle:
    """
    任务句柄，用于查询和控制并行任务
    """
    
    task_id: str
    status: TaskStatus  # PENDING, RUNNING, COMPLETED, FAILED, CANCELLED
    result: Optional[TaskResult]
    error: Optional[Exception]
    start_time: float
    end_time: Optional[float]
    
    async def wait(self, timeout: float = None) -> TaskResult:
        """等待任务完成"""
        pass
    
    async def cancel(self):
        """取消任务"""
        pass
    
    def is_done(self) -> bool:
        """任务是否完成"""
        pass
```

### 使用示例

#### 场景1：简单并行执行

```python
from aworld.core.parallel import ParallelExecutor

# 在Parent Aworld中
executor = ParallelExecutor()

# 提交多个并行任务
task_b = await executor.submit_task(
    task_id="task_b",
    directive="Analyze code quality of module X",
    agent_config={
        "tools": ["CAST_ANALYSIS", "CAST_SEARCH"],
        "model": "gpt-4o"
    }
)

task_c = await executor.submit_task(
    task_id="task_c",
    directive="Generate unit tests for module Y",
    agent_config={
        "tools": ["CAST_CODER", "CAST_SEARCH"],
        "model": "claude-sonnet-4"
    }
)

# 继续处理主任务A（非阻塞）
await process_main_task_a()

# 等待并行任务完成
results = await executor.wait_all(timeout=300)

# 合并结果
for result in results:
    if result.status == TaskStatus.COMPLETED:
        print(f"Task {result.task_id} completed: {result.output}")
```

#### 场景2：复杂依赖关系

```python
# 任务B和C并行执行，任务D依赖B和C的结果

# 提交B和C
task_b = await executor.submit_task(...)
task_c = await executor.submit_task(...)

# 等待B和C完成
result_b = await task_b.wait()
result_c = await task_c.wait()

# 基于B和C的结果启动D
task_d = await executor.submit_task(
    task_id="task_d",
    directive=f"Synthesize results: B={result_b.output}, C={result_c.output}",
    agent_config={...}
)

result_d = await task_d.wait()
```

#### 场景3：上下文合并控制

```python
from aworld.core.parallel import MergeConfig

# 提交任务（独立上下文）
task_handle = await executor.submit_task(
    task_id="isolated_task",
    directive="Process data with isolation",
    context_config={
        "isolated": True,
        "share_memory": False,  # 不共享parent memory
        "share_tools": True     # 共享parent tools
    }
)

result = await task_handle.wait()

# 选择性合并结果
await result.context.merge_back(MergeConfig(
    merge_memory=True,      # 合并新产生的memory
    merge_trajectory=False, # 不合并trajectory（保持隔离）
    merge_outputs=True      # 合并输出结果
))
```

### 与SubagentManager的区别

| 特性 | SubagentManager | ParallelExecutor |
|------|----------------|------------------|
| **目的** | 专业能力委派 | 并行任务执行 |
| **执行模式** | 阻塞式（等待结果） | 非阻塞（异步并行） |
| **上下文隔离** | Sub-context（部分隔离） | IsolatedContext（完全隔离） |
| **Agent类型** | 固定（developer, evaluator等） | 动态（任意配置） |
| **调度控制** | 无（立即执行） | 有（队列、优先级、超时） |
| **结果合并** | 自动合并 | 手动控制合并 |
| **典型用例** | "用developer分析代码" | "同时处理3个独立任务" |

### 设计优势

1. **清晰的职责分离：**
   - SubagentManager → 专业能力委派（垂直分工）
   - ParallelExecutor → 并行任务执行（水平扩展）

2. **避免循环委派：**
   - ParallelExecutor不依赖subagent机制
   - 不会出现"Aworld委派给自己"的混淆

3. **灵活的隔离控制：**
   - 可以精确控制哪些资源共享、哪些隔离
   - 可以选择性合并结果

4. **强大的调度能力：**
   - 任务队列、优先级、超时控制
   - 错误处理和重试机制
   - 资源限制（最大并行数、内存上限等）

## 实施计划

### Phase 1: 核心功能（MVP）

**目标：** 基础的并行执行和上下文隔离

**实现：**
1. `ParallelExecutor` 基础实现
   - `submit_task()` - 提交任务
   - `wait_task()` / `wait_all()` - 等待结果
   - 简单的任务队列（FIFO）

2. `IsolatedContext` 基础实现
   - 从 `AmniContext` 派生
   - 独立的memory和trajectory
   - 基础的merge_back()功能

3. 测试用例
   - 2个并行任务的执行和结果收集
   - 上下文隔离验证（memory不互相干扰）

### Phase 2: 高级调度（Production）

**目标：** 生产级的任务调度和错误处理

**实现：**
1. 高级调度特性
   - 任务优先级
   - 最大并行数限制
   - 动态负载均衡

2. 错误处理和恢复
   - 超时控制
   - 重试机制
   - 失败任务的清理

3. 监控和可观测性
   - 任务执行日志
   - 性能指标（执行时间、资源使用）
   - Dashboard（可选）

### Phase 3: 高级功能（Enhancement）

**目标：** 复杂场景支持

**实现：**
1. 任务依赖图（DAG）
   - 声明式依赖关系
   - 自动依赖解析和调度

2. 动态资源分配
   - 基于任务类型分配工具集
   - 动态调整context配置

3. 持久化和恢复
   - 任务状态持久化
   - 中断后恢复执行

## 配置示例

### parallelexecutor_config.yaml

```yaml
# ParallelExecutor配置
parallel_executor:
  # 全局设置
  max_concurrent_tasks: 5          # 最大并行任务数
  default_timeout: 300.0           # 默认超时（秒）
  enable_task_queue: true          # 启用任务队列
  queue_strategy: "fifo"           # 队列策略：fifo, priority, fair
  
  # 上下文隔离配置
  isolation:
    default_mode: "full"           # 默认隔离模式：full, partial, shared
    share_memory: false            # 是否共享parent memory
    share_tools: true              # 是否共享parent tools
    share_sandbox: true            # 是否共享sandbox
  
  # 错误处理
  error_handling:
    enable_retry: true             # 启用重试
    max_retries: 3                 # 最大重试次数
    retry_delay: 5.0               # 重试延迟（秒）
    fail_fast: false               # 一个任务失败是否取消其他任务
  
  # 监控和日志
  monitoring:
    enable_logging: true           # 启用详细日志
    log_level: "info"              # 日志级别
    emit_metrics: true             # 发送性能指标
    metrics_endpoint: null         # 指标收集端点（可选）
```

## API 设计

### 1. Tool接口（供LLM调用）

```python
@ToolFactory.register(
    name='parallel_execute',
    desc='Execute multiple tasks in parallel with isolated contexts'
)
class ParallelExecuteTool(AsyncTool):
    """
    LLM可调用的并行执行工具
    """
    
    async def do_step(self, action: List[ActionModel], **kwargs):
        """
        执行并行任务
        
        LLM调用示例：
        parallel_execute(
            tasks=[
                {
                    "id": "task_b",
                    "directive": "Analyze code quality",
                    "tools": ["CAST_ANALYSIS"]
                },
                {
                    "id": "task_c",
                    "directive": "Generate tests",
                    "tools": ["CAST_CODER"]
                }
            ],
            wait_all=True,
            timeout=300
        )
        """
        pass
```

### 2. Python API（供Agent代码使用）

```python
# 在Agent代码中使用
from aworld.core.parallel import ParallelExecutor, TaskConfig

executor = ParallelExecutor()

# 提交任务
handle = await executor.submit_task(
    TaskConfig(
        task_id="analysis_task",
        directive="Analyze module X",
        agent_config={"tools": ["CAST_ANALYSIS"]},
        context_config={"isolated": True},
        timeout=60.0
    )
)

# 等待结果
result = await handle.wait()
```

## 安全和限制

### 资源限制

```python
class ResourceLimits:
    """并行执行的资源限制"""
    max_concurrent_tasks: int = 5        # 最大并发任务数
    max_memory_per_task: int = 1024 * 1024 * 512  # 每任务最大内存（512MB）
    max_execution_time: float = 600.0    # 最大执行时间（10分钟）
    max_token_per_task: int = 100000     # 每任务最大token数
```

### 隔离保证

1. **Memory隔离：** 每个任务有独立的memory store
2. **Context隔离：** 独立的message history和state
3. **Tool隔离：** 可以为每个任务配置不同的工具集
4. **Sandbox隔离：** （可选）每个任务有独立的sandbox

## 未来扩展

### 1. 分布式执行

支持任务分发到多个worker节点：

```python
# 分布式配置
executor = ParallelExecutor(
    mode="distributed",
    worker_nodes=["worker1:8000", "worker2:8000"],
    load_balancing="round_robin"
)
```

### 2. 任务持久化

支持长时间运行的任务：

```python
# 持久化任务
handle = await executor.submit_task(
    ...,
    persistent=True,
    checkpoint_interval=60.0  # 每60秒保存checkpoint
)

# 从checkpoint恢复
recovered_handle = await executor.recover_task("task_id")
```

### 3. 可视化Dashboard

提供Web UI监控任务执行：
- 实时任务状态
- 执行时间分布
- 资源使用监控
- 任务依赖图可视化

## 参考

### 相关系统

1. **Celery** - Python分布式任务队列
2. **Dask** - Python并行计算框架
3. **Ray** - 分布式计算框架
4. **asyncio.gather()** - Python内置并行执行

### 技术选型

- **基础：** asyncio（Python异步IO）
- **调度：** asyncio.Queue + 自定义Scheduler
- **隔离：** contextvars（上下文变量）
- **监控：** structlog + prometheus（可选）

---

## 总结

**ParallelExecutor是SubagentManager的补充，不是替代：**

- **SubagentManager：** 专业能力委派（"用developer做开发"）
- **ParallelExecutor：** 并行任务执行（"同时做3件事"）

**不应该用"Aworld作为自己的subagent"来实现并行执行，原因：**
1. 语义不清晰（委派 vs 并行）
2. 循环委派风险
3. 缺乏调度和控制能力
4. 上下文隔离不完整

**正确的方案是实现专门的ParallelExecutor组件。**

---

**文档状态：** 📋 设计提案  
**优先级：** Medium（非urgent，但有明确价值）  
**预估工作量：** 
- Phase 1 (MVP): 2-3天
- Phase 2 (Production): 3-5天
- Phase 3 (Enhancement): 5-7天

**下一步：** 用户确认设计方案，然后进入Phase 1实施。
