import time

from aworld.core.task import TaskResponse
from aworld.output import Output
from aworld.runners.handler.base import DefaultHandler
from aworld.core.event.base import Message, Constants, TopicType
from aworld.logs.util import logger
from aworld.checkpoint import create_checkpoint, CheckpointMetadata
from aworld.checkpoint.inmemory import InMemoryCheckpointRepository
from aworld.core.context.utils import create_session_snapshot_from_runner


class DefaultInteractionHandler(DefaultHandler):

    def __init__(self, runner):
        super().__init__(runner)
        self.runner = runner
        self.checkpoint_repo = InMemoryCheckpointRepository()

    async def handle(self, message: Message):
        if message.category != Constants.INTERACTION:
            return
        if message.topic == TopicType.HUMAN_CONFIRM:
            logger.warn("=============== Need human confirm, pause execution ===============")
            if self.runner.task.outputs and message.payload:
                await self.runner.task.outputs.add_output(Output(data=message.payload))
            await self.handle_interrupt(message)
            self.runner._task_response = TaskResponse(answer=str(message.payload),
                                                      success=True,
                                                      id=self.runner.task.id,
                                                      time_cost=(time.time() - self.runner.start_time),
                                                      usage=self.runner.context.token_usage)
            await self.runner.stop()
            return

        if message.topic == "interrupt":
            await self.handle_interrupt(message)
        elif message.topic == "resume":
            await self.handle_resume(message)
        elif message.topic == "user_input":
            await self.handle_user_input(message)
        elif message.topic == "external_command":
            await self.handle_external_command(message)
        # ... 其他 topic ...
        yield message

    async def handle_interrupt(self, message: Message):
        runner = self.runner
        
        # 使用新的Context功能创建完整的会话状态快照
        session_snapshot = create_session_snapshot_from_runner(runner)
        
        # 添加observation数据
        observation_data = None
        if hasattr(runner, 'observation') and runner.observation:
            if hasattr(runner.observation, 'model_dump'):
                observation_data = runner.observation.model_dump()
            else:
                observation_data = str(runner.observation)
        
        values = {
            "session_snapshot": session_snapshot,
            "observation": observation_data,
            "task_info": {
                "task_id": runner.task.id if hasattr(runner.task, 'id') else None,
                "task_name": runner.task.name if hasattr(runner.task, 'name') else None,
                "input": runner.task.input if hasattr(runner.task, 'input') else None,
                "endless_threshold": runner.task.endless_threshold if hasattr(runner.task, 'endless_threshold') else None
            },
            "runner_info": {
                "start_time": runner.start_time if hasattr(runner, 'start_time') else None,
                "name": runner.name if hasattr(runner, 'name') else None,
                "tool_names": runner.tool_names if hasattr(runner, 'tool_names') else None
            }
        }
        
        metadata = CheckpointMetadata(
            session_id=runner.context.session_id if hasattr(runner.context, 'session_id') else str(getattr(runner.context, 'session_id', '')),
            task_id=runner.task.id if hasattr(runner.task, 'id') else str(getattr(runner.task, 'id', ''))
        )
        
        checkpoint = create_checkpoint(values=values, metadata=metadata)
        self.checkpoint_repo.put(checkpoint)
        
        # 获取当前步骤信息用于日志
        cur_step = "N/A"
        if session_snapshot.get("swarm_context"):
            cur_step = session_snapshot["swarm_context"].get("cur_step", "N/A")
        
        logger.info(f"[InteractionHandler] Complete checkpoint saved for session {metadata.session_id}, task {metadata.task_id}, step {cur_step}")
        logger.info(f"[InteractionHandler] Snapshot includes: {list(session_snapshot.keys())}")

    async def handle_resume(self, message: Message):
        # 处理恢复事件
        # TODO: 实现从checkpoint恢复会话状态的功能
        logger.info("[InteractionHandler] Resume functionality not yet implemented")
        pass

    async def handle_user_input(self, message: Message):
        # 处理用户输入事件
        logger.info(f"[InteractionHandler] Received user input: {message.payload}")
        pass

    async def handle_external_command(self, message: Message):
        # 处理外部指令事件
        logger.info(f"[InteractionHandler] Received external command: {message.payload}")
        pass 