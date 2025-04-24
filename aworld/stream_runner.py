# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import copy

from aworld.core.task import Task, Config
from aworld.output import StreamingOutputs
from aworld.runner import Runners


class RunConfig:
    pass


DEFAULT_MAX_TURNS = 20

class StreamRunner:

    @staticmethod
    def run_streamed(
            task: Task,
    ) -> StreamingOutputs:
        """
        One Agent with Tool Event Loop
        """
        if not task.conf:
            task.conf = Config()

        streamed_result = StreamingOutputs(
            input=task.input,
            usage={},
            is_complete=False
        )
        task.outputs = streamed_result

        streamed_result._run_impl_task = asyncio.create_task(
            Runners.run_task(task)
        )
        return streamed_result
