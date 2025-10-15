from typing import List

from ... import ApplicationContext
from . import Neuron
from .neuron_factory import neuron_factory


@neuron_factory.register(name="workspace", desc="Workspace neuron", prio=10)
class WorkspaceNeuron(Neuron):
    """处理工作空间相关属性的Neuron"""

    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        """格式化工作空间信息"""
        items = []

        # 工作空间
        workspace = context.workspace
        if workspace:
            workspace_info = f"<workspace_id>{getattr(workspace, 'workspace_id', 'unknown')}</workspace_id>"
            workspace_info += f"<workspace_type>{getattr(workspace, 'workspace_type', 'unknown')}</workspace_type>"
            items.append(workspace_info)

        return items

    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None,
                     **kwargs) -> str:
        """组合工作空间信息"""
        if not items:
            items = await self.format_items(context, namespace, **kwargs)

        return "<workspace_info>\n" + "\n".join(items) + "\n</workspace_info>\n"
