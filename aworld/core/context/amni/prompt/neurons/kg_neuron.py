from typing import List

from amnicontext import ApplicationContext
from . import Neuron


class AugmentKgKeywordsNeuron(Neuron):
    """
    通过KG增强 Neuron
    - 宏观概念
    - 微观概念
    """

    async def desc(self, context: ApplicationContext, namespace: str = None, **kwargs) -> str:
        return await super().desc(context, namespace, **kwargs)

