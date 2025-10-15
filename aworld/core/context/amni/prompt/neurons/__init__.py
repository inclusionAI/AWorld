from abc import ABC, abstractmethod
from typing import List

from ... import ApplicationContext


class Neurons:
    ALL = '*'
    BASIC = 'basic'
    HUMAN = 'human'
    FACT = 'fact'
    HISTORY = 'history'
    TASK = "task"
    TODO = "todo"
    ACTION_INFO = "action_info"
    CONVERSATION_HISTORY = "conversation_history"
    WORKING_DIR = "working_dir"
    WORKSPACE = "workspace"
    GRAPH = "graph"
    ENTITY = "entity"

class Neuron(ABC):
    """
    神经元抽象类
    """

    async def desc(self, context: ApplicationContext, namespace: str = None, **kwargs) -> str:
        return ""

    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        pass

    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None,
                     **kwargs) -> str:
        pass


# 导入注册表和工厂
from .neuron_factory import NeuronFactory, neuron_factory
from aworld.utils.common import scan_packages

# 自动扫描并注册所有 Neuron 类
scan_packages("aworld.core.context.amni.prompt.neurons", [Neuron])

# 导出
__all__ = [
    'Neuron',
    'Neurons',
    'NeuronFactory',
    'neuron_factory',
]
