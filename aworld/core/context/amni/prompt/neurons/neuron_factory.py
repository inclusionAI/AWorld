from typing import List, Optional, Type, Dict, Any

from aworld.core.factory import Factory
from . import Neuron, Neurons
from ..neurons_binding import agent_neuron_factory
from ... import logger

DEFAULT_COMPONENTS = [Neurons.BASIC, Neurons.TASK, Neurons.WORKING_DIR, Neurons.TODO, Neurons.ACTION_INFO]


class NeuronFactory(Factory[Type[Neuron]]):
    """
    Neuron factory class for managing and retrieving Neuron instances
    
    Uses decorator-based registration pattern similar to HandlerFactory.
    """

    def __init__(self):
        super().__init__(type_name="neuron")
        self._neuron_instances: Dict[str, Neuron] = {}  # Cache for neuron instances

    def __call__(self, name: str, **kwargs) -> Optional[Neuron]:
        """
        Get or create a neuron instance by name
        
        Args:
            name: Neuron name
            **kwargs: Additional arguments (not used currently)
            
        Returns:
            Neuron instance or None if not found
        """
        if name not in self._cls:
            logger.warning(f"Neuron '{name}' not registered")
            return None
            
        # Return cached instance if available
        if name in self._neuron_instances:
            return self._neuron_instances[name]
            
        # Create new instance
        neuron_class = self._cls[name]
        neuron_instance = neuron_class()
        
        # Set name attribute
        if not hasattr(neuron_instance, 'name'):
            neuron_instance.name = name
            
        # Cache the instance
        self._neuron_instances[name] = neuron_instance
        
        return neuron_instance

    def get_neuron(self, name: str) -> Optional[Neuron]:
        """
        Get neuron instance by name
        
        Args:
            name: Neuron name
            
        Returns:
            Neuron instance or None if not found
        """
        return self(name)

    def get_all_neurons(self, namespace: str = None, agent_class: Type = None) -> List[Neuron]:
        """
        Get all neuron instances
        
        Args:
            namespace: Optional namespace filter
            agent_class: Optional agent class filter (not used currently)
            
        Returns:
            List of neuron instances sorted by priority
        """
        neurons = []
        for name in self._cls.keys():
            neuron = self(name)
            if neuron:
                neurons.append(neuron)

        # Sort by priority
        neurons.sort(key=lambda x: self._prio.get(x.name, 0))

        # Filter by namespace if specified
        if namespace is not None:
            neurons = self._filter_by_namespace(neurons, namespace)

        return neurons

    def get_neurons_by_names(self, names: List[str]) -> List[Neuron]:
        """
        Get neuron instances by name list
        
        Args:
            names: List of neuron names
            
        Returns:
            List of neuron instances
        """
        neurons = []
        for name in names:
            neuron = self.get_neuron(name)
            if neuron:
                neurons.append(neuron)
        return neurons

    def _get_neuron_name(self, neuron: Neuron) -> str:
        """
        Get neuron name from instance
        
        Args:
            neuron: Neuron instance
            
        Returns:
            Neuron name or None if not found
        """
        if hasattr(neuron, 'name'):
            return neuron.name
            
        for name, instance in self._neuron_instances.items():
            if instance is neuron:
                return name
        return None

    def get_neuron_names(self) -> List[str]:
        """
        Get all neuron names sorted by priority
        
        Returns:
            List of neuron names
        """
        names = list(self._cls.keys())
        # Sort by priority
        names.sort(key=lambda x: self._prio.get(x, 0))
        return names

    def filter_neurons(self,
                       min_priority: Optional[int] = None,
                       max_priority: Optional[int] = None) -> List[Neuron]:
        """
        Filter neurons by priority range
        
        Args:
            min_priority: Minimum priority (inclusive)
            max_priority: Maximum priority (inclusive)
            
        Returns:
            List of filtered neuron instances
        """
        neurons = self.get_all_neurons()

        if min_priority is not None or max_priority is not None:
            filtered_neurons = []
            for neuron in neurons:
                neuron_name = self._get_neuron_name(neuron)
                if neuron_name:
                    priority = self._prio.get(neuron_name, 0)
                    if min_priority is not None and priority < min_priority:
                        continue
                    if max_priority is not None and priority > max_priority:
                        continue
                    filtered_neurons.append(neuron)
            return filtered_neurons

        return neurons

    def _filter_by_namespace(self, neurons: List[Neuron], namespace: str) -> List[Neuron]:
        """
        Filter neurons by namespace
        
        Args:
            namespace: Namespace to filter by
            neurons: List of neurons to filter
            
        Returns:
            Filtered list of neurons
        """
        neuron_config = agent_neuron_factory.get_agent_component_config(namespace)
        # If not registered, only return default components
        if not neuron_config:
            return [neuron for neuron in neurons if neuron.name in DEFAULT_COMPONENTS]
            
        filtered_neurons = []
        for neuron in neurons:
            neuron_name = self._get_neuron_name(neuron)
            neuron_names = neuron_config.get('neurons', [])
            if neuron_name in neuron_names or (neuron_names and neuron_names[0] == Neurons.ALL):
                filtered_neurons.append(neuron)
        return filtered_neurons

    def get_neurons_by_namespace(self, namespace: str) -> List[Neuron]:
        """
        Get all neurons for a specific namespace
        
        Args:
            namespace: Namespace to filter by
            
        Returns:
            List of neuron instances for the namespace
        """
        return self.get_all_neurons(namespace=namespace)

    def get_neuron_strategy(self, name: str, namespace: str = None):
        """
        Get strategy configuration for a neuron
        
        Args:
            name: Neuron name
            namespace: Optional namespace for strategy override
            
        Returns:
            Strategy configuration
        """
        # Strategy is now stored in ext_info
        ext_info = self.get_ext_info(name)
        if not ext_info:
            return None

        if namespace and 'strategies' in ext_info and namespace in ext_info.get('strategies', {}).keys():
            return ext_info.get('strategies', {}).get(namespace)

        return ext_info.get('default_strategy')

# global neuron factory
neuron_factory = NeuronFactory()
