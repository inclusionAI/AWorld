# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import traceback
from typing import Any, Dict, Tuple

from aworld.config import ToolConfig
from aworld.core.common import ToolActionInfo, ParamInfo, Observation, ActionModel, ActionResult
from aworld.core.context.amni import ApplicationContext
from aworld.core.event.base import Message
from aworld.core.tool.action import ToolAction
from aworld.core.tool.base import ToolFactory, AsyncTool
from aworld.logs.util import logger
from aworld.tools.utils import build_observation
from .base import RewardResult

REWARD = "reward"


class RewardExecuteAction(ToolAction):
    """Definition of Reward execute supported action."""
    REWARD_EVALUATE = ToolActionInfo(
        name="reward_evaluate",
        input_params={},
        desc="The main purpose of this tool is to pass given content to the reward function for evaluation.")


@ToolFactory.register(name=REWARD,
                      desc=REWARD,
                      supported_action=RewardExecuteAction)
class RewardTool(AsyncTool):
    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        """Init reward tool."""
        super(RewardTool, self).__init__(conf, **kwargs)
        self.cur_observation = None
        self.content = None
        self.keyframes = []
        self.init()
        self.step_finished = True
        logger.info(f"RewardTool|初始化完成, tool_name={self.name()}")

    async def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
        Observation, dict[str, Any]]:
        logger.info(f"RewardTool|开始重置, seed={seed}, options={options}")
        await super().reset(seed=seed, options=options)

        await self.close()
        self.step_finished = True
        observation = build_observation(observer=self.name(),
                                       ability=RewardExecuteAction.REWARD_EVALUATE.value.name)
        logger.info(f"RewardTool|重置完成, step_finished={self.step_finished}")
        return observation, {}

    def init(self) -> None:
        self.initialized = True
        logger.debug(f"RewardTool|工具初始化完成, initialized={self.initialized}")

    async def close(self) -> None:
        logger.debug(f"RewardTool|关闭工具")

    async def finished(self) -> bool:
        result = self.step_finished
        logger.debug(f"RewardTool|检查完成状态, step_finished={result}")
        return result

    async def do_step(self, actions: list[ActionModel], message: Message = None, **kwargs) -> Tuple[
        Observation, float, bool, bool, Dict[str, Any]]:

        logger.info(f"RewardTool|开始执行do_step, actions数量={len(actions) if actions else 0}, kwargs={kwargs}")
        self.step_finished = False
        reward = 0.
        fail_error = ""
        observation = build_observation(observer=self.name(),
                                        ability=RewardExecuteAction.REWARD_EVALUATE.value.name)
        info = {}
        try:
            if not actions:
                logger.error("RewardTool|actions为空，无法执行评估")
                raise ValueError("actions is empty")
            action = actions[0]
            logger.debug(f"RewardTool|使用action: {action}")

            context: ApplicationContext = message.context
            logger.debug(f"RewardTool|从context获取数据")
            traj_validation_dataset = context.get('traj_validation_dataset')
            running_traj = context.get('running_traj')
            tmp_file_path = context.get('tmp_file_path')
            reward_function = context.get('reward_function')

            logger.info(f"RewardTool|准备调用奖励函数, "
                       f"traj_validation_dataset={'存在' if traj_validation_dataset else 'None'}, "
                       f"running_traj={'存在' if running_traj else 'None'}, "
                       f"tmp_file_path={tmp_file_path}, "
                       f"reward_function={'存在' if reward_function else 'None'}")

            if reward_function is None:
                logger.error("RewardTool|reward_function为None，无法执行评估")
                raise ValueError("reward_function is None")

            logger.info("RewardTool|开始调用奖励函数进行评估")
            reward_result = await reward_function(context=context, validation_file_path=traj_validation_dataset,
                                                  traj_file_path=running_traj, tmp_file_path=tmp_file_path)
            logger.info(f"RewardTool|奖励函数执行完成, reward_result={reward_result}")

            # 格式化奖励结果详情
            result_content = f"Score: {reward_result.score:.2f}\n" \
                           f"Trajectory Output: {reward_result.traj_output}\n" \
                           f"Ground Truth: {reward_result.ground_truth}\n" \
                           f"Reasoning: {reward_result.reasoning}"

            observation.content = result_content
            observation.action_result.append(
                ActionResult(is_done=True,
                             success=True,
                             content=result_content,
                             error=f"",
                             keep=False))
            reward = 1.
            logger.info(f"RewardTool|do_step执行成功, reward={reward}, observation.content已设置")
        except Exception as e:
            fail_error = str(e)
            logger.error(f"RewardTool|do_step执行失败: {fail_error}")
            logger.warn(f"RewardTool|详细错误信息: {traceback.format_exc()}")
        finally:
            self.step_finished = True
            logger.debug(f"RewardTool|do_step完成, step_finished={self.step_finished}")
        info["exception"] = fail_error
        info.update(kwargs)
        result = (observation, reward, kwargs.get("terminated", False),
                 kwargs.get("truncated", False), info)
        logger.info(f"RewardTool|do_step返回结果, reward={reward}, terminated={result[2]}, truncated={result[3]}, "
                   f"has_exception={'是' if fail_error else '否'}")
        return result

