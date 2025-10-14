from abc import ABC, abstractmethod
from typing import List

from amnicontext import ApplicationContext


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

    # 根据对象结构，格式化
    @abstractmethod
    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        pass

    # 拼接成完整的对象结构
    @abstractmethod
    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None,
                     **kwargs) -> str:
        pass


# 导入注册表和工厂
from .neuron_factory import NeuronFactory, neuron_factory

# 导入所有组件（这会触发注册）
from .task_neuron import TaskHistoryNeuron
from .fact_neuron import FactsNeuron
from .workspace_neuron import WorkspaceNeuron
from .history_neuron import HistoryNeuron
from .working_dir_neuron import WorkingDirNeuron
from .basic_neuron import BasicNeuron
from .human_neuron import HumanNeuron
from .todo_neuron import TodoNeuron
from .action_info_neuron import ActionInfoNeuron
from .entity_neuron import EntitiesNeuron

# 导出所有组件类
__all__ = [
    'Neuron',
    'NeuronFactory',
    'neuron_factory',
    'TaskHistoryNeuron',
    'FactsNeuron',
    'WorkspaceNeuron',
    'HistoryNeuron',
    'WorkingDirNeuron',
    'BasicNeuron',
    'HumanNeuron',
    'TodoNeuron',
    'ActionInfoNeuron',
    'EntitiesNeuron'
]
