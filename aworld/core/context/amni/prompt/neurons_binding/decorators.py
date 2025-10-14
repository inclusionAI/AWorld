from typing import List, Type, Dict, Optional


class AgentNeuronRegistry:
    def __init__(self):
        self._registry: Dict[str, Type] = {}

    def register(self, namespace: str, neurons: List[str], **kwargs):
        def decorator(cls: Type):
            # 检查是否已经存在 _component_neuron_config 属性
            if not hasattr(cls, '_component_neuron_config'):
                cls._component_neuron_config = {}

            # 存储配置信息
            cls._component_neuron_config.update({
                'neurons': neurons or [],
                **kwargs
            })

            # 添加获取神经元配置的方法
            if not hasattr(cls, 'get_component_neuron_config'):
                @classmethod
                def get_component_neuron_config(cls):
                    """获取组件的神经元配置"""
                    return getattr(cls, '_component_neuron_config', {})

                cls.get_component_neuron_config = get_component_neuron_config

            # 添加获取神经元名称列表的方法
            if not hasattr(cls, 'get_component_neuron_names'):
                @classmethod
                def get_component_neuron_names(cls):
                    """获取组件的神经元名称列表"""
                    config = cls.get_component_neuron_config()
                    return config.get('neurons', [])

                cls.get_component_neuron_names = get_component_neuron_names

            # 添加获取命名空间的方法
            if not hasattr(cls, 'get_component_namespace'):
                def get_component_namespace(self):
                    """获取组件的命名空间（使用 agent_name）"""
                    return getattr(self, 'name', None) or getattr(self, 'agent_name', None) or namespace or cls.__name__

                cls.get_component_namespace = get_component_namespace

            # 向注册表中注册组件
            self._registry[namespace] = cls

            return cls

        return decorator

    def get(self, namespace: str) -> Optional[Type]:
        """获取注册的组件类"""
        return self._registry.get(namespace)

registry = AgentNeuronRegistry()

def agent_neuron(namespace: str, neurons: List[str] = None, **kwargs):
    return registry.register(namespace=namespace, neurons=neurons, **kwargs)
