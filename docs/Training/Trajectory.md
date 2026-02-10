AWorld runtime captures every step across offline and online runs. Each task yields a complete trajectory—every LLM call, action, and reward—so you can synthesize training samples, audit performance, and iterate with confidence.

## Complete Task Trajectories
Tasks unfold over many LLM calls. The framework captures every step, giving you a full trajectory.

```python
import asyncio
from aworld.runner import Runners
from aworld.core.task import Task
from aworld.logs.util import logger
import json

# refer the section above for agent constrution 
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

## Single-Step Introspection
Need finer control? Call `step()` to inspect one action/response pair at a time. This lets you inject intermediate rewards during training, enabling richer, more flexible learning signals.

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
