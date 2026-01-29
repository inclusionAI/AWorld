# coding: utf-8
from aworld.core.context.amni import ApplicationContext
from aworld.experimental.metalearning.knowledge.knowledge import append_traj_id_to_session_artifact
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.logs.util import logger
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PostTaskCallHook


@HookFactory.register(name="MetaLearningTrajectoryRecordHook", desc="Meta-learning trajectory record Hook, records task_id when task is completed")
class MetaLearningTrajectoryRecordHook(PostTaskCallHook):
    """
    Meta-learning trajectory record Hook
    
    When meta_learning_config.enabled is True, calls append_traj_id_to_session_artifact after task completion
    to record the trajectory task_id that needs to be learned into MULTI_TURN_TASK_ID_DATA
    """

    async def exec(self, message: Message, context: Context = None) -> Message:
        """
        Execute Hook, check configuration and record task_id
        
        Args:
            message: Message object
            context: Context object
            
        Returns:
            Message: Returns the original message
        """
        if not context:
            return message

        # Only process ApplicationContext
        if not isinstance(context, ApplicationContext):
            # Try to get root context
            if hasattr(context, 'root') and isinstance(context.root, ApplicationContext):
                context = context.root
            else:
                return message

        try:
            # Get configuration
            config = context.get_config()
            if not config:
                return message

            # Check if enabled
            if not config.agent_config.meta_learning.enabled:
                logger.debug(f"Meta-learning is not enabled, skip recording task_id: task_id={context.task_id}")
                return message

            # Get task_id
            task_id = context.task_id
            if not task_id:
                logger.warning("Unable to get task_id, skip recording trajectory")
                return message

            # Call append_traj_id_to_session_artifact to record task_id
            logger.info(f"Recording trajectory task_id to MULTI_TURN_TASK_ID_DATA: task_id={task_id}, session_id={context.session_id}")
            await append_traj_id_to_session_artifact(context=context, task_id=task_id)
            logger.info(f"Successfully recorded trajectory task_id: task_id={task_id}")

        except Exception as e:
            logger.error(f"Failed to record trajectory task_id: task_id={context.task_id if context else 'unknown'}, error={e}")
            # Don't raise exception to avoid affecting main flow

        return message
