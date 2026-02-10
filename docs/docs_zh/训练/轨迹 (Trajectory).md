
AWorld运行时捕获离线和在线运行的每一步。每个任务都会产生一个完整的轨迹——每个LLM调用、操作和奖励——这样你就可以合成训练样本、评估性能，并迭代优化。

## 任务完整轨迹
任务有多次LLM或工具调用，或多个Agent之间的交互。AWorld会记录每一步，为您提供完整的轨迹。

```python
import asyncio
from aworld.runner import Runners
from aworld.core.task import Task
from aworld.logs.util import logger
import json

# 定义代理
searcher = Agent(...)

if __name__ == "__main__":
    async def test_complete_trajectory():
        task = Task(
            input="Use google search tool to answer the question: the news about AI today.",
            agent=searcher
        )

        responses = await Runners.run_task(task)
        resp = responses[task.id]
        logger.info(f"task answer: {resp.answer}")
        logger.info(f"task trajectory: {json.dumps(resp.trajectory, ensure_ascii=False)}")
    asyncio.run(test_complete_trajectory())
```

## 单步交互
需要更精细的控制吗？调用`step（）`一次检查一个动作/响应对。这使您可以在训练过程中注入中间奖励，从而获得更丰富、更灵活的学习信号。

```python
import os
import asyncio
from aworld.runner import Runners
from aworld.core.task import Task
from aworld.logs.util import logger
import json
from aworld.config import TaskConfig, TaskRunMode

# refer the section above for agent constrution 
searcher = Agent(...)

if __name__ == "__main__":
    async def test_single_step_introspection():
        task = Task(
            input="Use google search tool to answer the question: the news about AI today.",
            agent=searcher,
            conf=TaskConfig(
                resp_carry_context=True,
                run_mode=TaskRunMode.INTERACTIVE
            )
        )

        trajectory_log = os.path.join(os.path.dirname(__file__), "trajectory_log.txt")
        is_finished = False
        step = 1
        while not is_finished:
            with open(trajectory_log, "a", encoding="utf-8") as traj_file:
                is_finished, observation, response = await Runners.step(task)
                traj_file.write(f"Step {step}\n")
                traj_file.write(json.dumps(response.trajectory, ensure_ascii=False, indent=2))
                traj_file.write("\n\n")
                step += 1
    asyncio.run(test_single_step_introspection())
```
