from typing import List, Type, Dict, Optional


class AgentNeuronRegistry:
    def __init__(self):
        self._registry: Dict[str, Type] = {}

    def register(self, namespace: str, neurons: List[str], **kwargs):
        def decorator(cls: Type):
            # Check if _component_neuron_config attribute already exists
            if not hasattr(cls, '_component_neuron_config'):
                cls._component_neuron_config = {}

            # Store configuration information
            cls._component_neuron_config.update({
                'neurons': neurons or [],
                **kwargs
            })

            # Add method to get neuron configuration
            if not hasattr(cls, 'get_component_neuron_config'):
                @classmethod
                def get_component_neuron_config(cls):
                    """Get component's neuron configuration"""
                    return getattr(cls, '_component_neuron_config', {})

                cls.get_component_neuron_config = get_component_neuron_config

            # Add method to get neuron names list
            if not hasattr(cls, 'get_component_neuron_names'):
                @classmethod
                def get_component_neuron_names(cls):
                    """Get component's neuron names list"""
                    config = cls.get_component_neuron_config()
                    return config.get('neurons', [])

                cls.get_component_neuron_names = get_component_neuron_names

            # Add method to get namespace
            if not hasattr(cls, 'get_component_namespace'):
                def get_component_namespace(self):
                    """Get component's namespace (using agent_name)"""
                    return getattr(self, 'name', None) or getattr(self, 'agent_name', None) or namespace or cls.__name__

                cls.get_component_namespace = get_component_namespace

            # Register component in registry
            self._registry[namespace] = cls

            return cls

        return decorator

    def get(self, namespace: str) -> Optional[Type]:
        """Get registered component class"""
        return self._registry.get(namespace)

registry = AgentNeuronRegistry()

def agent_neuron(namespace: str, neurons: List[str] = None, **kwargs):
    return registry.register(namespace=namespace, neurons=neurons, **kwargs)
