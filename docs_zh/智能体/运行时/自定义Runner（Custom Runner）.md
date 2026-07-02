AWorld框架提供了灵活的运行时基础设施，使得开发者可以在不修改框架代码的前提下，实现任意复杂的执行逻辑。Runner作为运行时核心，同样允许开发者创建完全自定义的任务执行Runner和引擎，以适应特定的业务需求。

### 何时需要自定义Runner
- ✅ 需要特殊的执行流程（如并行、条件分支、循环）

- ✅ 需要自定义的错误处理和恢复机制

- ✅ 需要集成特定的外部系统

- ✅ 需要性能优化或资源控制

- ✅ 需要实现特定的调度策略

- ✅ 需要完全定制化的输出和监控

### 何时不需要自定义Runner
- ❌ 改变后处理逻辑 → 使用Handler

- ❌ 修改参数 → 使用Hooks或Callbacks

- ❌ 改变输出格式 → 使用OutputProcessHook

- ❌ 自定以轨迹 → 扩展TrajectoryStrategy

### 创建自定义Runner
#### Agent任务
建议基于TaskRunner

##### 继承TaskRunner
```python
from aworld.runners.task_runner import TaskRunner
from aworld.core.task import Task, TaskResponse
from aworld.core.context.base import Context

class MyCustomRunner(TaskRunner):
    """自定义Runner模板"""
    
    def __init__(self, task: Task, *args, **kwargs):
        super().__init__(task, *args, **kwargs)
        # 添加自定义初始化
        self.custom_state = {}
    
    async def pre_run(self):
        """准备工作"""
        await super().pre_run()
        # 自定义初始化逻辑
        logger.info("Custom runner preparing...")
    
    async def do_run(self, context: Context = None) -> TaskResponse:
        """核心执行逻辑"""
        # 这是必须实现的抽象方法
        pass
    
    async def post_run(self):
        """清理工作"""
        await super().post_run()
        # 自定义清理逻辑
        logger.info("Custom runner cleaning up...")
```

##### 自定义核心执行流程
```python
async def do_run(self, context: Context = None) -> TaskResponse:
    """实现自己的执行逻辑，返回TaskResponse对象"""
    observation = self.observation
    
    try:
        # 1. 执行任务的核心逻辑
        result = await self._execute_custom_logic(observation)
        
        # 2. 生成响应
        response = TaskResponse(
            task_id=self.task.id,
            content=result,
            status="success"
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Execution failed: {e}")
        return TaskResponse(
            task_id=self.task.id,
            content=str(e),
            status="failed"
        )

async def _execute_custom_logic(self, observation):
    """自定义执行逻辑"""
    pass
```



#### 其他类任务
##### 继承Runner
```python
class TreeSearchRunner(Runner):
    """基于树搜索的自定义Runner"""
    
    def __init__(self, task: Task, *args, **kwargs):
        super().__init__(task, *args, **kwargs)
        self.max_depth = self.conf.get('max_depth', 3)
        self.max_breadth = self.conf.get('max_breadth', 3)
        self.search_tree = {}
    
```

##### 实现核心执行
```python
async def do_run(self, context: Context = None) -> TaskResponse:
        """执行树搜索"""
        observation = self.observation
        
        # 执行深度优先搜索
        best_result = await self._dfs(observation, depth=0)
        
        return TaskResponse(
            task_id=self.task.id,
            content=best_result,
            status="success"
        )
    
    async def _dfs(self, state, depth):
        """深度优先搜索"""
        if depth >= self.max_depth:
            return state
        
        # 获取可能的动作
        agent = self.swarm.ordered_agents[0]
        actions = await self._get_possible_actions(agent, state)
        
        # 探索前max_breadth个动作
        best_result = state
        best_score = self._evaluate(state)
        
        for action in actions[:self.max_breadth]:
            # 执行动作
            next_state = await self._execute_action(action, state)
            
            # 递归搜索
            result = await self._dfs(next_state, depth + 1)
            
            # 评估结果
            score = self._evaluate(result)
            if score > best_score:
                best_score = score
                best_result = result
        
        return best_result
    
    async def _get_possible_actions(self, agent, state):
        """获取可能的动作"""
        obs = Observation(content=str(state))
        output = await agent.run(obs)
        return output.get('actions', [])
    
    async def _execute_action(self, action, state):
        """执行动作"""
        tool = self.tools.get(action['tool'])
        result = await tool.step(action['args'])
        return result.get('next_state', state)
    
    def _evaluate(self, state):
        """评估状态"""
        # 自定义评估逻辑
        return len(str(state))
```

### 最佳实践
✅ **建议：清晰的职责划分**

**模块化设计**

```python
class WellDesignedRunner(TaskRunner):
    """流程和模块清晰"""
    
    async def do_run(self, context):
        # 只处理主要流程
        return await self._main_execution(context)
    
    async def _main_execution(self, context):
        """主执行逻辑"""
        observation = ...
        
        # 1. 执行前置处理
        processed_obs = await self._preprocess(observation)
        
        # 2. 执行主要逻辑
        for step in range(self.max_steps):
            output = await self._execute_step(processed_obs, step)
            results.append(output)
            
            if self._should_stop(output):
                break
            
            # 3. 更新观察
            processed_obs = self._update_observation(processed_obs, output)
        
        # 4. 执行后置处理
        final_result = await self._postprocess(results)
        ...

    async def _execute_step(self, obs, step):
        pass
    
    def _should_stop(self, output):
        pass

    def _update_observation(self, ob, output):
        pass
    
    async def _preprocess(self):
        pass
    
    async def _postprocess(self):
        pass
```

❌ **不推荐：单个方法过于庞大**

**耦合严重，难以单测**

```python
class PoorlyDesignedRunner(TaskRunner):
    async def do_run(self, context):
        # 所有逻辑都在里面执行
        # ... 几百行代码 ...
        pass
```

**示例：**

```python
class TestableRunner(TaskRunner):
    """易于测试的Runner"""
    
    def __init__(self, task: Task, *args, **kwargs):
        super().__init__(task, *args, **kwargs)
        self._inject_dependencies()
    
    def _inject_dependencies(self):
        """依赖注入，便于测试时mock"""
        self.http_client = getattr(self, 'http_client', HttpClient())
        self.cache = getattr(self, 'cache', DefaultCache())
        self.url = getattr(self, 'url', ...)
    
    async def do_run(self, context: Context = None) -> TaskResponse:
        """可测试的执行"""
        # 使用注入的依赖
        result = await self.http_client.get(self.url)
        cached_result = self.cache.get(result)
        
        return TaskResponse(
            task_id=self.task.id,
            content=cached_result,
            status="success"
        )
```

