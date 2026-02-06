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
        logger.info(f"RewardTool|Initialization completed, tool_name={self.name()}")

    async def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
        Observation, dict[str, Any]]:
        logger.info(f"RewardTool|Starting reset, seed={seed}, options={options}")
        await super().reset(seed=seed, options=options)

        await self.close()
        self.step_finished = True
        observation = build_observation(observer=self.name(),
                                       ability=RewardExecuteAction.REWARD_EVALUATE.value.name)
        logger.info(f"RewardTool|Reset completed, step_finished={self.step_finished}")
        return observation, {}

    def init(self) -> None:
        self.initialized = True
        logger.debug(f"RewardTool|Tool initialization completed, initialized={self.initialized}")

    async def close(self) -> None:
        logger.debug(f"RewardTool|Closing tool")

    async def finished(self) -> bool:
        result = self.step_finished
        logger.debug(f"RewardTool|Checking completion status, step_finished={result}")
        return result

    async def do_step(self, actions: list[ActionModel], message: Message = None, **kwargs) -> Tuple[
        Observation, float, bool, bool, Dict[str, Any]]:

        logger.info(f"RewardTool|Starting do_step execution, action count={len(actions) if actions else 0}, kwargs={kwargs}")
        self.step_finished = False
        reward = 0.
        fail_error = ""
        observation = build_observation(observer=self.name(),
                                        ability=RewardExecuteAction.REWARD_EVALUATE.value.name)
        info = {}
        try:
            if not actions:
                logger.error("RewardTool|actions is empty, cannot perform evaluation")
                raise ValueError("actions is empty")
            action = actions[0]
            logger.debug(f"RewardTool|Using action: {action}")

            context: ApplicationContext = message.context
            logger.debug(f"RewardTool|Getting data from context")
            traj_validation_dataset = context.get('traj_validation_dataset')
            running_traj = context.get('running_traj')
            tmp_file_path = context.get('tmp_file_path')
            reward_function = context.get('reward_function')

            logger.info(f"RewardTool|Preparing to call reward function, "
                       f"traj_validation_dataset={'exists' if traj_validation_dataset else 'None'}, "
                       f"running_traj={'exists' if running_traj else 'None'}, "
                       f"tmp_file_path={tmp_file_path}, "
                       f"reward_function={'exists' if reward_function else 'None'}")

            if reward_function is None:
                logger.error("RewardTool|reward_function is None, cannot perform evaluation")
                raise ValueError("reward_function is None")

            logger.info("RewardTool|Starting reward function evaluation")
            reward_result = await reward_function(context=context, validation_file_path=traj_validation_dataset,
                                                  traj_file_path=running_traj, tmp_file_path=tmp_file_path)
            logger.info(f"RewardTool|Reward function execution completed, reward_result={reward_result}")

            # Format reward result details
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
            logger.info(f"RewardTool|do_step executed successfully, reward={reward}, observation.content has been set")
        except Exception as e:
            fail_error = str(e)
            logger.error(f"RewardTool|do_step execution failed: {fail_error}")
            logger.warn(f"RewardTool|Detailed error information: {traceback.format_exc()}")
        finally:
            self.step_finished = True
            logger.debug(f"RewardTool|do_step completed, step_finished={self.step_finished}")
        info["exception"] = fail_error
        info.update(kwargs)
        result = (observation, reward, kwargs.get("terminated", False),
                 kwargs.get("truncated", False), info)
        logger.info(f"RewardTool|do_step returning result, reward={reward}, terminated={result[2]}, truncated={result[3]}, "
                   f"has_exception={'yes' if fail_error else 'no'}")
        return result

