# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import List

from aworld.core.task import Task

from aworld.logs.util import logger
from aworld.utils.common import new_instance, snake_to_camel


def choose_runner(task: Task):
    """Choose the correct runner to run the task.

    Args:
        task: A task that contains agents, tools and datas.

    Returns:
        Runner instance or exception.
    """
    runner_cls = task.runner_cls
    if runner_cls:
        return new_instance(runner_cls, task)
    else:
        if task.swarm:
            task.swarm.reset(task.input)
            topology = task.swarm.topology_type
        else:
            topology = "sequence"

        runner = new_instance(
            f"aworld.runners.call_driven_runner.{snake_to_camel(topology)}Runner", task)
        return runner


def endless_detect(records: List[str], endless_threshold: int, root_agent_name: str):
    """A very simple implementation of endless loop detection.

    Args:
        records: Call sequence of agent.
        endless_threshold: Threshold for the number of repetitions.
        root_agent_name: Name of the entrance agent.
    """
    if not records:
        return False

    threshold = endless_threshold
    last_agent_name = root_agent_name
    count = 1
    for i in range(len(records) - 2, -1, -1):
        if last_agent_name == records[i]:
            count += 1
        else:
            last_agent_name = records[i]
            count = 1

        if count >= threshold:
            logger.warning("detect loop, will exit the loop.")
            return True

    if len(records) > 6:
        last_agent_name = None
        # latest
        for j in range(1, 3):
            for i in range(len(records) - j, 0, -2):
                if last_agent_name and last_agent_name == (records[i], records[i - 1]):
                    count += 1
                elif last_agent_name is None:
                    last_agent_name = (records[i], records[i - 1])
                    count = 1
                else:
                    last_agent_name = None
                    break

                if count >= threshold:
                    logger.warning(f"detect loop: {last_agent_name}, will exit the loop.")
                    return True

    return False
