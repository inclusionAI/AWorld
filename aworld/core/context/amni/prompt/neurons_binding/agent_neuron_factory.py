from typing import List, Type, Dict, Any, Optional

from .decorators import registry


class AgentNeuronFactory:
    """Component Neuron 工厂类，用于管理和获取带有 component_neuron 注解的 Agent 的神经元组件"""

    def __init__(self):
        self._registry = registry

    def get_agent_component_neurons(self, namespace: str) -> List[str]:
        agent_class = self.get_registered_agent(namespace)
        if not agent_class:
            return []
        return agent_class.get_component_neuron_names()

    def get_agent_component_config(self, namespace: str) -> Dict[str, Any]:
        agent_class = self.get_registered_agent(namespace)
        if not agent_class:
            return {}
        return agent_class.get_component_neuron_config()

    def get_registered_agent(self, agent_name: str) -> Optional[Type]:
        return self._registry.get(agent_name)


# 创建全局工厂实例
agent_neuron_factory = AgentNeuronFactory()
