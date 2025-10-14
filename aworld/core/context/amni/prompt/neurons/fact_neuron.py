from typing import List

from ... import ApplicationContext
from . import Neuron


class FactsNeuron(Neuron):
    """处理事实相关属性的Neuron"""

    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        """格式化事实信息"""
        facts = await context.retrival_facts()
        if not facts:
            return []

        formatted_facts = []
        for fact in facts:
            formatted_facts.append(f"<fact>{fact.content}</fact>")

        return formatted_facts

    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None,
                     **kwargs) -> str:
        """组合事实信息"""
        if not items:
            items = await self.format_items(context, namespace, **kwargs)

        return "<facts>\n" + "\n".join(items) + "\n</facts>\n"
