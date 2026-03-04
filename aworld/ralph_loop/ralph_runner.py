# coding: utf-8
# Copyright (c) inclusionAI.
from aworld.core.task import Runner, Task, TaskResponse
from aworld.logs.util import logger
from aworld.runners.event_runner import TaskEventRunner
from aworld.utils.run_util import exec_tasks
from aworld.ralph_loop.config import RalphConfig
from aworld.ralph_loop.detect.detector import create_stop_detector
from aworld.ralph_loop.state.types import LoopState, to_loop_context
from aworld.ralph_loop.types import CompletionCriteria


class RalphRunner(Runner):
    """Pipeline mode of RalphRunner implementing Ralph pattern.

    The RALPH pattern consists of:
    - R (Run): Execute tasks with strategic planning
    - A (Analyze): Validate outputs against multiple criteria
    - L (Learn): Reflect on execution and extract insights
    - P (Plan): Replan based on feedback and learnings
    - H (Halt): Detect termination conditions
    """

    def __init__(self, task: Task, completion_criteria: CompletionCriteria = None, **kwargs):
        super().__init__(**kwargs)
        self.task = Task(input=task) if isinstance(task, str) else task
        self.ralph_config = self.task.conf if self.task.conf else RalphConfig.create(
            model_config=task.swarm.ordered_agents[0].conf.llm_config if task.swarm else task.agent.conf.llm_config)
        self.completion_criteria = completion_criteria or CompletionCriteria()

        # State management
        self.loop_context = None
        self.task_context = None

        # Initialize components
        self._init_stop_detector()

    async def pre_run(self):
        self.loop_context = to_loop_context(await TaskEventRunner.build_context(self.task),
                                            completion_criteria=self.completion_criteria,
                                            loop_state=LoopState(confirmation_threshold=1),
                                            work_dir=self.ralph_config.workspace)

    async def do_run(self):
        execution_result = TaskResponse()
        while True:
            cur_task = self.task
            self.loop_context.iteration += 1
            iter_num = self.loop_context.iteration

            # 1. Check stop conditions
            logger.info(f"Iteration {iter_num} Stop condition check...")
            stop_decision = await self.stop_detector.should_stop(self.loop_context)
            if stop_decision.should_stop:
                logger.info(f"Loop terminated: {stop_decision.stop_type}, Reason: {stop_decision.reason}")
                break

            # 2. Execute task
            logger.info(f"Iteration {iter_num} Executing task...")
            try:
                execution_result = await self._execute_task(cur_task, iter_num=iter_num)
            except:
                logger.error(f"Error executing task: {cur_task.id}")
                # error process

        return execution_result

    def _init_stop_detector(self):
        detectors = self.ralph_config.stop_condition.stop_detectors or []
        self.stop_detector = create_stop_detector(custom_detectors=detectors)

    async def _execute_task(self, task: Task, iter_num: int) -> TaskResponse:
        self.task_context = to_loop_context(
            await self.loop_context.read_to_task_context(task=task, iter_num=iter_num,
                                                         reuse_context=self.ralph_config.reuse_context),
            work_dir=self.ralph_config.workspace
        )
        task.context = self.task_context

        results = await exec_tasks(tasks=[task])
        execution_result: TaskResponse = results.get(task.id)
        await self.loop_context.add_file(filename=f"{task.id}_{iter_num}", content=execution_result.answer)
        await self.loop_context.write_to_loop_context(content='',
                                                      task_context=self.task_context,
                                                      iter_num=iter_num,
                                                      reuse_context=self.ralph_config.reuse_context)
        return execution_result
