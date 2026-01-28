# coding: utf-8
import sys
from pathlib import Path

from aworld.core.context.base import Context
from aworld.core.event.base import Message, Constants, TopicType
from aworld.events.util import send_message
from aworld.logs.util import logger
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PostTaskCallHook
from aworldspace.backgroundtask.backgroundtask import execute_background_task

# Add pipelines directory to Python path to enable aworldspace imports
# _pipelines_path = Path(__file__).parent.parent.parent
# if _pipelines_path.exists() and str(_pipelines_path) not in sys.path:
#     sys.path.insert(0, str(_pipelines_path))

from aworld.experimental.metalearning.optimizer.meta_learning_strategy import meta_learning_strategy
from aworld.experimental.metalearning.reward.gaia_reward import gaia_match_reward


@HookFactory.register(name="PostTaskCallLearningHook", desc="任务完成后的元学习Hook")
class PostTaskCallLearningHook(PostTaskCallHook):

    async def run_learning_task(self, context: Context):
        """执行学习任务，调用learningtask模块中的run_learning_task函数"""
        result = await meta_learning_strategy(
            context=context,
            traj_validation_dataset="http://localhost:5173/cust_res/gaia_validation.jsonl",
            reward_function=gaia_match_reward,
            learning_strategy=meta_learning_strategy,
        )
        return result

    async def exec(self, message: Message, context: Context = None) -> Message:
        """
        执行后置任务Hook，异步启动学习任务但不等待完成

        注意：此方法会立即返回，学习任务在后台异步执行。
        如果学习任务失败（如curl超时），会记录警告日志但不会影响主流程的返回。
        """
        # 使用异步任务执行机制，不等待任务完成
        # execute_background_task 返回 asyncio.Task，在后台异步执行
        async def on_complete_callback(result):
            await context.outputs.mark_completed()

        task = execute_background_task(
            self.run_learning_task(context),
            task_name=f"learning_task_{context.task_id}",
            on_complete=on_complete_callback,
            on_error=lambda e: logger.warning(
                f"Background learning task failed for task_id={context.task_id}, "
                f"error={e}. This does not affect the main process."
            )
        )
        logger.info(
            f"Learning task created for task_id={context.task_id}, "
            f"task_name=learning_task_{context.task_id}. Running in background."
        )

        # 等待后台任务完成
        # try:
        #     await task
        #     logger.info(
        #         f"Learning task completed for task_id={context.task_id}, "
        #         f"task_name=learning_task_{context.task_id}."
        #     )
        # except Exception as e:
        #     logger.warning(
        #         f"Learning task failed for task_id={context.task_id}, "
        #         f"error={e}. This does not affect the main process."
        #     )
        # 返回消息
        return message

