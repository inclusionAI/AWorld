Hooks（钩子）是AWorld框架中的运行时生命周期拦截机制，是实现开闭原则（Open-Closed Principle）的设计体现。它允许开发者在任务执行的关键点插入自定义逻辑，而无需修改框架核心代码。

### Hooks的核心作用
Hooks可以在以下场景发挥作用：

1. **监控和日志** - 记录关键执行点的数据
2. **性能分析** - 测量各个阶段的耗时
3. **数据转换** - 在不同阶段转换或清洗数据
4. **业务逻辑注入** - 插入特定的业务规则
5. **错误处理和恢复** - 在错误时进行干预
6. **审计和追踪** - 记录所有关键操作

### 为什么需要Hooks？
在没有Hooks的架构中，一般需要修改框架代码来添加功能。使用Hooks的方式允许通过注册钩子函数来扩展框架功能，无需修改主流程代码。合理使用Hooks，开发者可以优雅地扩展框架功能，同时保持代码的清晰和可维护性。

1. **非侵入式的扩展机制** - 无需修改框架代码
2. **清晰的生命周期拦截** - 在关键点插入逻辑
3. **灵活的组合方式** - 支持Hook链和条件执行
4. **强大的监控能力** - 完整的事件追踪
5. **可维护的代码** - 关注点分离

### Hook机制
#### Hook点（Hook Point）
Hook点是任务执行过程中的**关键时刻**。框架内部已定义系列的Hook点：

```python
class HookPoint:
    START = "start"                    # 任务开始
    FINISHED = "finished"              # 任务完成
    ERROR = "error"                    # 任务出错
    PRE_LLM_CALL = "pre_llm_call"      # LLM调用前
    POST_LLM_CALL = "post_llm_call"    # LLM调用后
    PRE_TOOL_CALL = "pre_tool_call"    # Tool调用前
    POST_TOOL_CALL = "post_tool_call"  # Tool调用后
    OUTPUT_PROCESS = "output_process"  # 输出处理
```

#### Hook（钩子）
Hook是一个可执行的单元，在特定的Hook点被调用：

```python
class Hook:
    
    @abc.abstractmethod
    def point(self) -> str:
        """返回这个Hook所属的Hook点"""
        
    @abc.abstractmethod
    async def exec(self, message: Message, context: Context = None) -> Message:
        """
        执行Hook逻辑
        
        Args:
            message: 当前的消息对象
            context: 执行上下文
            
        Returns:
            修改后的消息对象（或保持原样）
        """
```

#### Hook链（Hook Chain）
在同一个Hook点，可以注册多个Hook，它们会**按顺序依次执行**：

```plain
消息输入
   ↓
[Hook1执行] → 可能修改消息
   ↓
[Hook2执行] → 可能进一步修改消息
   ↓
[Hook3执行] → 可能再次修改消息
   ↓
消息输出
```

#### Hook注册
Hook可以自动注册到`HookFactory`：

```python
@HookFactory.register(name="PreLLMCallContextProcessHook",
                      desc="PreLLMCallContextProcessHook")
class PreLLMCallContextProcessHook(PreLLMCallHook):
    def name(self):
        return convert_to_snake("PreLLMCallContextProcessHook")

    async def exec(self, message: Message, context: Context = None) -> Message:
        # and do something
        pass
```

#### Hook执行
```python
async def run_hooks(self, message: Message, hook_point: str) -> AsyncGenerator[Message, None]:
    """执行指定hook点的所有Hook"""
    if not self.hooks:
        return
    
    hooks = self.hooks.get(hook_point, [])
    for hook in hooks:
        try:
            msg = await hook.exec(message)
            if msg:
                yield msg
        except Exception as e:
            logger.error(f"Hook execution failed: {e}")
```

### 最佳实践
```python
from aworld.runners.hook.hooks import StartHook, FinishedHook

class MonitoringStartHook(StartHook):
    """清晰的名称和注释。

    检测并记录启动时的使用。
    """
    async def exec(self, message, context):
        task = context.get_task()
        print(f"✓ Task started: {task.id}")
        print(f"  Input: {task.input}")
        return message

class MonitoringFinishedHook(FinishedHook):
    """健壮的处理。

    
    """
    async def exec(self, message, context):
        try:
            task = context.get_task()
            print(f"✓ Task finished: {task.id}")
            print(f"  Result: {message.payload}")
        except:
            pass
        return message

# 使用
task = Task(name="test", input="What is AI?", agent=agent)
runner = TaskEventRunner(task)

runner.hooks = {
    HookPoint.START: [MonitoringStartHook()],
    HookPoint.FINISHED: [MonitoringFinishedHook()]
}

response = await runner.run()
```

