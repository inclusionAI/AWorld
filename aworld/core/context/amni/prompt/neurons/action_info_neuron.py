import time
from typing import List

from ... import ApplicationContext, logger
from . import Neuron
from .neuron_factory import neuron_factory


@neuron_factory.register(name="action_info", desc="Action info neuron", prio=3)
class ActionInfoNeuron(Neuron):
    """Neuron for handling action information related properties"""

    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        """Format action information"""
        start_time = time.perf_counter()
        
        context._workspace._load_workspace_data(load_artifact_content=False)
        artifacts = await context._workspace.query_artifacts(search_filter={
            "context_type": "actions_info"
        })
        logger.info(f"📚 Retrieved actions info: {len(artifacts)} artifacts")
        
        result = [f"  <knowledge id='{artifact.artifact_id}' summary='{artifact.summary}'></knowledge>\n" for artifact in artifacts]
        
        # Log execution time if debug mode is enabled
        if context.get_config() and context.get_config().debug_mode:
            elapsed_time = time.perf_counter() - start_time
            logger.info(f"⏱️  ActionInfoNeuron.format_items() execution time: {elapsed_time:.4f}s")
        
        return result


    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None,
                     **kwargs) -> str:
        """Combine action information"""
        start_time = time.perf_counter()
        
        actions_info = (
            "\nBelow is the actions information, including both successful and failed experiences, "
            "as well as key knowledge and insights obtained during the process.\n"
            "Make full use of this information:\n"
            "<knowledge_list>\n"
        )
        if not items:
            items = await self.format_items(context, namespace, **kwargs)
        actions_info += "\n".join(items)
        actions_info += f"\n</knowledge_list>\n"
        actions_info += f"<tips>\n"
        actions_info += f"you can use get_knowledge(knowledge_id_xxx) to got detail content\n"
        actions_info += f"</tips>\n"
        
        result = actions_info
        
        # Log execution time if debug mode is enabled
        if context.get_config() and context.get_config().debug_mode:
            elapsed_time = time.perf_counter() - start_time
            logger.info(f"⏱️  ActionInfoNeuron.format() execution time: {elapsed_time:.4f}s")
        
        return result