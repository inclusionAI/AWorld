The AWorld framework provides a flexible runtime infrastructure that enables developers to implement arbitrarily complex execution logic **without modifying the core framework code**. As the heart of the runtime, the **Runner** is also fully extensible—developers can create entirely custom task executors and engines to meet specific business requirements.

### When to Implement a Custom Runner
+ ✅ You need a **specialized execution flow** (e.g., parallel execution, conditional branching, loops)
+ ✅ You require **custom error handling and recovery mechanisms**
+ ✅ You must **integrate with a specific external system**
+ ✅ You need **performance optimization or fine-grained resource control**
+ ✅ You want to implement a **custom scheduling policy**
+ ✅ You require **fully customized output formatting or monitoring logic**

### When _Not_ to Implement a Custom Runner
+ ❌ To modify **post-processing logic** → Use a **Handler**
+ ❌ To adjust **parameters or inputs** → Use **Hooks** or **Callbacks**
+ ❌ To change **output format** → Use an **OutputProcessHook**
+ ❌ To customize **trajectory recording** → Extend the **TrajectoryStrategy**

### Creating a Custom Runner
#### For Agent Tasks
It is recommended to base your implementation on `**TaskRunner**`.

##### Inherit from `TaskRunner` 
Override the necessary methods to define your custom execution behavior.

```python
from aworld.runners.task_runner import TaskRunner
from aworld.core.task import Task, TaskResponse
from aworld.core.context.base import Context

class MyCustomRunner(TaskRunner):
    """Custom Runner"""
    
    def __init__(self, task: Task, *args, **kwargs):
        super().__init__(task, *args, **kwargs)
        # add custom init
        self.custom_state = {}
    
    async def pre_run(self):
        """prepare"""
        await super().pre_run()
        # custom logic
        logger.info("Custom runner preparing...")
    
    async def do_run(self, context: Context = None) -> TaskResponse:
        """core logic"""
        pass
    
    async def post_run(self):
        """clean"""
        await super().post_run()
        # custom logic
        logger.info("Custom runner cleaning up...")
```

##### Customize the core execution process
```python
async def do_run(self, context: Context = None) -> TaskResponse:
    """Custom response，return TaskResponse."""
    observation = self.observation
    
    try:
        # 1. execution core logic
        result = await self._execute_custom_logic(observation)
        
        # 2. gen response
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
    """Custom logic"""
    pass
```

#### Other types of tasks
##### Inheriting Runner
```python
class TreeSearchRunner(Runner):
    """Customization based on tree search runner."""
    
    def __init__(self, task: Task, *args, **kwargs):
        super().__init__(task, *args, **kwargs)
        self.max_depth = self.conf.get('max_depth', 3)
        self.max_breadth = self.conf.get('max_breadth', 3)
        self.search_tree = {}
    
```

##### Execution Core
```python
async def do_run(self, context: Context = None) -> TaskResponse:
        """Perform tree search."""
        observation = self.observation
        
        # depth search first
        best_result = await self._dfs(observation, depth=0)
        
        return TaskResponse(
            task_id=self.task.id,
            content=best_result,
            status="success"
        )
    
    async def _dfs(self, state, depth):
        """depth search first"""
        if depth >= self.max_depth:
            return state
        
        agent = self.swarm.ordered_agents[0]
        # get action
        actions = await self._get_possible_actions(agent, state)
        
        # Exploring the max-breadth actions before exploration
        best_result = state
        best_score = self._evaluate(state)
        
        for action in actions[:self.max_breadth]:
            # execute action
            next_state = await self._execute_action(action, state)
            
            # Recursively searching 
            result = await self._dfs(next_state, depth + 1)
            
            # Evaluate result
            score = self._evaluate(result)
            if score > best_score:
                best_score = score
                best_result = result
        
        return best_result
    
    async def _get_possible_actions(self, agent, state):
        """Obtain possible actions."""
        obs = Observation(content=str(state))
        output = await agent.run(obs)
        return output.get('actions', [])
    
    async def _execute_action(self, action, state):
        """Execute action."""
        tool = self.tools.get(action['tool'])
        result = await tool.step(action['args'])
        return result.get('next_state', state)
    
    def _evaluate(self, state):
        """Evaluate the status."""
        # custom evaluate process
        return len(str(state))
```

### Best Practices
✅ **Suggestion: Clear division of responsibilities**

**modular design**

```python
class WellDesignedRunner(TaskRunner):
    """Clear processes and modules"""
    
    async def do_run(self, context):
        # 只处理主要流程
        return await self._main_execution(context)
    
    async def _main_execution(self, context):
        """main logic"""
        observation = ...
        
        # 1. pre process
        processed_obs = await self._preprocess(observation)
        
        # 2. execute main logic
        for step in range(self.max_steps):
            output = await self._execute_step(processed_obs, step)
            results.append(output)
            
            if self._should_stop(output):
                break
            
            # 3. update observation
            processed_obs = self._update_observation(processed_obs, output)
        
        # 4. post process
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

❌ **Not recommended: a single method is too large**

**Severe coupling, difficult to measure individually**

```python
class PoorlyDesignedRunner(TaskRunner):
    async def do_run(self, context):
        # All logic is executed inside
        # ... Hundreds of lines of code ...
        pass
```

