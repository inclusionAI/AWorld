from typing import List

from ... import ApplicationContext
from . import Neuron
from .neuron_factory import neuron_factory


@neuron_factory.register(name="fact", desc="Facts neuron", prio=3, prompt_augment_strategy="append")
class FactsNeuron(Neuron):
    """Neuron for handling fact related properties"""

    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        """Format fact information"""
        facts = await context.retrival_facts()
        if not facts:
            return []

        formatted_facts = []
        for fact in facts:
            formatted_facts.append(f"<fact>{fact.content}</fact>")

        return formatted_facts

    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None,
                     **kwargs) -> str:
        """Combine fact information"""
        if not items:
            items = await self.format_items(context, namespace, **kwargs)

        return "<facts>\n" + "\n".join(items) + "\n</facts>\n"
