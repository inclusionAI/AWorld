# coding: utf-8
import traceback

from aworld.core.context.amni import ApplicationContext
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.experimental.metalearning.knowledge.learning_knowledge import (
    LearningKnowledge,
    append_traj_id_to_session_artifact,
    save_context_artifact,
    TrajType,
)
from aworld.logs.util import logger
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PostTaskCallHook


@HookFactory.register(name="LearningKnowledgeGenerationHook",
                      desc="Learning knowledge generation Hook, generates knowledge artifacts and records task_id when task is completed")
class LearningKnowledgeGenerationHook(PostTaskCallHook):
    """
    Learning knowledge generation Hook
    
    When meta_learning_config.enabled is True, this hook:
    1. Generates knowledge artifacts (graph, meta, exp data) after task completion
    2. Records the trajectory task_id that needs to be learned into MULTI_TURN_TASK_ID_DATA
    """

    async def exec(self, message: Message, context: Context = None) -> Message:
        """
        Execute Hook, generate knowledge artifacts and record task_id
        
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
            agent = context.swarm.communicate_agent[0]
            if not agent or not agent.conf:
                return message

            # Check if enabled
            config = agent.conf
            if not config.meta_learning_config.enabled:
                logger.debug(f"Meta-learning is not enabled, skip knowledge generation: task_id={context.task_id}")
                return message

            # Get task_id
            task_id = context.task_id
            if not task_id:
                logger.warning("Unable to get task_id, skip knowledge generation")
                return message

            # Generate knowledge artifacts
            await self._generate_knowledge_artifacts(context)

            # Record task_id
            logger.info(
                f"Recording trajectory task_id to MULTI_TURN_TASK_ID_DATA: task_id={task_id}, session_id={context.session_id}")
            await append_traj_id_to_session_artifact(context=context, task_id=task_id)
            logger.info(f"Successfully recorded trajectory task_id: task_id={task_id}")

        except Exception as e:
            logger.error(
                f"Failed to generate knowledge or record task_id: task_id={context.task_id if context else 'unknown'}, error={e} {traceback.format_exc()}")

        return message

    async def _generate_knowledge_artifacts(self, context: Context):
        """
        Generate knowledge artifacts (graph, meta, exp data)
        
        Args:
            context: Context object
        """
        if isinstance(context, ApplicationContext):
            context = context.root

        session_id = context.session_id
        if not session_id:
            logger.warning("LearningKnowledgeGenerationHook: session_id is None, skip artifact creation")
            return

        traj_data = await LearningKnowledge.get_running_exp(context)
        if traj_data is None:
            logger.info("LearningKnowledgeGenerationHook: traj_data is None, skip graph generation")
            return

        graph_data = LearningKnowledge.parse_traj_to_graph(context, traj_data)
        logger.info(f"graph_data: {graph_data}")

        meta_data = await LearningKnowledge.get_running_meta(context, context.task_id)
        meta_artifact_id = await save_context_artifact(context, TrajType.META_DATA, meta_data)
        exp_artifact_id = await save_context_artifact(context, TrajType.EXP_DATA,
                                                      LearningKnowledge.convert_to_store_data(traj_data))
        graph_data_artifact_id = await save_context_artifact(context, TrajType.GRAPH_DATA, graph_data)

        logger.info(
            f"LearningKnowledgeGenerationHook: Successfully created graph structure artifact, exp_data_artifact_id={exp_artifact_id}, "
            f"graph_data_artifact_id={graph_data_artifact_id}, "
            f"meta_data_artifact_id={meta_artifact_id}, "
            f"nodes={len(graph_data.get('nodes', []))}, edges={len(graph_data.get('edges', []))}")
