# coding: utf-8
import traceback
from typing import Any
from typing import Dict, List

from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation, ActionModel
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni import TaskInput, workspace_repo, \
    AmniContextConfig
from aworld.core.context.amni.services.trajectory_service import TrajType
from aworld.core.context.amni.services.trajectory_service import get_artifact_data
from aworld.core.event.base import Message
from aworld.experimental.metalearning.learning.meta_learning_strategy import meta_learning_strategy
from aworld.experimental.metalearning.reward.gaia_reward import gen_simple_message_reward_function
from aworld.logs.util import logger


class OptimizerAgent(Agent):
    """
    Optimizer Agent - Responsible for executing meta-learning strategies, analyzing and optimizing agent configurations
    
    This agent retrieves trajectory data, validation datasets, reward functions and other parameters from the context,
    then calls meta_learning_strategy to execute meta-learning tasks.
    """

    def __init__(self, **kwargs):
        """Initialize Optimizer Agent"""
        super().__init__(**kwargs)
        logger.info(f"OptimizerAgent|Initialization completed, agent_name={self.name()}")


    async def build_task_context(self, parent_context: ApplicationContext, task_input: TaskInput,
                                 context_config: AmniContextConfig,
                                 **kwargs) -> ApplicationContext:

        # 1. init workspace
        workspace = await workspace_repo.get_session_workspace(session_id=task_input.session_id)

        # 2. init context
        context = await ApplicationContext.from_input(task_input, workspace=workspace, context_config=context_config)

        # 3. outputs
        context.put('outputs', parent_context.outputs)
        return context


    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {},
                           message: Message = None, **kwargs) -> List[ActionModel]:
        """
        Execute meta-learning optimization strategy
        
        Args:
            observation: Observation object
            info: Additional information
            message: Message object containing context information
            
        Returns:
            List of action models containing execution results
        """
        logger.info(f"OptimizerAgent|Starting async_policy execution")
        
        try:
            # Parse observation.content to get feedback
            feedback = observation.content
            logger.info(f"OptimizerAgent|Received user feedback: {feedback}")

            # Validate context
            if not message or not message.context:
                raise ValueError("message or message.context is required")
            
            if not isinstance(message.context, ApplicationContext):
                raise ValueError("context is not ApplicationContext")

            context: ApplicationContext = message.context

            # Put feedback into context
            if feedback:
                context.put("user_feedback", feedback)

            # Get necessary parameters from context
            traj_validation_dataset = context.get('traj_validation_dataset')

            # Simplified reward function that directly returns user input
            tmp_file_path = context.get('tmp_file_path') or 'data/learning'
            reward_function = gen_simple_message_reward_function(feedback)

            logger.info(f"OptimizerAgent|Preparing to call meta_learning_strategy, "
                        f"traj_validation_dataset={'exists' if traj_validation_dataset else 'None'}, "
                        f"tmp_file_path={tmp_file_path}, "
                        f"reward_function={'exists' if reward_function else 'None'}, ")

            if reward_function is None:
                logger.warning("OptimizerAgent|reward_function is None, will use default strategy execution")
                # If there is no reward_function, execution can still proceed, but reward evaluation may not be possible

            # Get learning_session_id and learning_task_id
            learning_session_id = context.get('learning_session_id')
            learning_task_id = context.get('learning_task_id')

            if not learning_session_id:
                learning_session_id = context.session_id
            
            # If learning_task_id is not directly provided, try to get it from MULTI_TURN_TASK_ID_DATA
            if not learning_task_id and learning_session_id:
                try:
                    multi_turn_content = await get_artifact_data(context=context, artifact_id=TrajType.MULTI_TURN_TASK_ID_DATA)
                    
                    if multi_turn_content:
                        # Parse task_id list (one task_id per line)
                        task_ids = [line.strip() for line in str(multi_turn_content).strip().split('\n') if line.strip()]
                        
                        if task_ids:
                            # Get the last round's task_id
                            learning_task_id = task_ids[-1]
                            logger.info(f"OptimizerAgent|Retrieved last round task_id from MULTI_TURN_TASK_ID_DATA: {learning_task_id}")
                        else:
                            logger.warning(f"OptimizerAgent|MULTI_TURN_TASK_ID_DATA is empty: session_id={learning_session_id}")
                    else:
                        logger.warning(f"OptimizerAgent|MULTI_TURN_TASK_ID_DATA not found: session_id={learning_session_id}")
                except Exception as e:
                    logger.error(f"OptimizerAgent|Failed to get MULTI_TURN_TASK_ID_DATA: session_id={learning_session_id}, error={e}")

            # Call meta_learning_strategy to execute meta-learning task
            logger.info("OptimizerAgent|Starting to call meta_learning_strategy to execute meta-learning task")
            result = await meta_learning_strategy(
                context=context,
                learning_session_id=learning_session_id,
                learning_task_id=learning_task_id,
                reward_function=reward_function,
                tmp_file_path=tmp_file_path
            )
            logger.info(f"OptimizerAgent|meta_learning_strategy execution completed, result={result}")

            # Format result
            if result:
                result_content = f"Meta Learning Optimization Completed:\n{result}"
            else:
                result_content = "Meta Learning completed (skipped due to high reward score or no optimization needed)"

            logger.info(f"OptimizerAgent|async_policy execution succeeded, result length: {len(result_content) if result_content else 0}")
            
            # Return result
            return [ActionModel(
                agent_name=self.id(),
                policy_info=result_content
            )]
            
        except Exception as e:
            fail_error = str(e)
            logger.error(f"OptimizerAgent|async_policy execution failed: {fail_error}")
            logger.warn(f"OptimizerAgent|Detailed error information: {traceback.format_exc()}")
            
            # Return error information
            return [ActionModel(
                agent_name=self.id(),
                policy_info=f"Execution failed: {fail_error}"
            )]

