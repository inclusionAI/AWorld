from typing import List

from ... import ApplicationContext
from . import Neuron
from .neuron_factory import neuron_factory


@neuron_factory.register(name="kg", desc="Knowledge graph neuron", prio=6)
class AugmentKgKeywordsNeuron(Neuron):
    """
    通过KG增强 Neuron
    - 宏观概念
    - 微观概念
    """

    async def desc(self, context: ApplicationContext, namespace: str = None, **kwargs) -> str:
        return await super().desc(context, namespace, **kwargs)



