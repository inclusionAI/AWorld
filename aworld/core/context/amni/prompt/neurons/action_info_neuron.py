from typing import List

from amnicontext import ApplicationContext, logger
from . import Neuron


class ActionInfoNeuron(Neuron):
    """处理工作空间相关属性的Neuron"""

    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        """格式化工作空间信息"""
        context._workspace._load_workspace_data()
        artifacts = await context._workspace.query_artifacts(search_filter={
            "context_type": "actions_info"
        })
        logger.info(f"get_actions_info: {len(artifacts)}")
        return [f"  <knowledge id='{artifact.artifact_id}' summary='{artifact.summary}'></knowledge>\n" for artifact in artifacts]


    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None,
                     **kwargs) -> str:
        """组合工作空间信息"""
        actions_info = (
            "\nBelow is the actions information, including both successful and failed experiences, "
            "as well as key knowledge and insights obtained during the process ，"
            "\n充分使用这些信息:\n"
            "<knowledge_list>\n"
        )
        if not items:
            items = await self.format_items(context, namespace, **kwargs)
        actions_info += "\n".join(items)
        actions_info += f"\n</knowledge_list>\n"
        actions_info += f"<tips>\n"
        actions_info += f"you can use get_knowledge(knowledge_id_xxx) to got detail content\n"
        actions_info += f"</tips>\n"
        return actions_info