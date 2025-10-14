import importlib
import traceback
from typing import List, Optional, Dict, Type

from . import Neuron, Neurons
from ..neurons_binding import agent_neuron_factory
from ... import get_amnicontext_config, logger

DEFAULT_COMPONENTS = [Neurons.BASIC, Neurons.TASK, Neurons.WORKING_DIR, Neurons.TODO, Neurons.ACTION_INFO]


class NeuronFactory:
    """Neuron工厂类，用于管理和获取Neuron实例"""

    _neurons: Dict[str, Dict] = {}  # 存储组件元信息
    _neuron_instances: Dict[str, Neuron] = {}  # 存储组件实例

    def __init__(self):
        # 读取初始化配置并注册组件
        neuron_config = get_amnicontext_config().neuron_config
        if neuron_config:
            self._register_from_config(neuron_config)

    def _register_from_config(self, neuron_configs):
        """从配置中注册组件"""
        for config in neuron_configs:
            self.register_from_config(config)

    def register_from_config(self, config):
        """从AmniContextNeuronConfig注册组件"""
        try:
            # 动态导入组件类
            module_path, class_name = config.type.rsplit('.', 1)
            module = importlib.import_module(module_path)
            neuron_class = getattr(module, class_name)

            # 创建组件实例
            neuron_instance = neuron_class()

            # 注册到_neurons
            self._neurons[config.name] = {
                'class': neuron_class,
                'instance': neuron_instance,
                'priority': config.priority,
                'default_strategy': config.default_strategy,
                'strategies': config.strategies,
                'config': config
            }

            if not hasattr(neuron_instance, 'default_strategy'):
                neuron_instance.default_strategy = config.default_strategy

            if not hasattr(neuron_instance, 'strategies'):
                neuron_instance.strategies = config.strategies

            if not hasattr(neuron_instance, 'name'):
                neuron_instance.name = config.name

            # 注册到_neuron_instances
            self._neuron_instances[config.name] = neuron_instance
            logger.info(f"注册组件 {config.name} 成功")

        except Exception as e:
            print(f"注册组件 {config.name} 失败: {e} {traceback.format_exc()}")

    def get_neuron(self, name: str) -> Optional[Neuron]:
        """根据名称获取组件实例"""
        return self._neuron_instances.get(name)

    def get_all_neurons(self, namespace: str = None, agent_class: Type = None) -> List[Neuron]:
        """获取所有组件实例"""
        # 首先从注册表获取基础组件
        """
        获取所有组件实例

        Args:
            strategy: 策略类型过滤，支持"append"和"rerank"
        """
        neurons = []
        for name, info in self._neurons.items():
            neurons.append(self._neuron_instances[name])

        # 按优先级排序
        neurons.sort(key=lambda x: self._neurons[self._get_neuron_name(x)]['priority'])

        # 如果指定了namespace，则按namespace过滤
        if namespace is not None:
            neurons = self._filter_by_namespace(neurons, namespace)

        return neurons

    def get_neurons_by_names(self, names: List[str]) -> List[Neuron]:
        """根据名称列表获取组件实例"""
        neurons = []
        for name in names:
            neuron = self.get_neuron(name)
            if neuron:
                neurons.append(neuron)
        return neurons

    @classmethod
    def _get_neuron_name(self, neuron: Neuron) -> str:
        """根据组件实例获取组件名称"""
        for name, instance in self._neuron_instances.items():
            if instance is neuron:
                return name
        return None

    def get_neuron_names(self) -> List[str]:
        """获取所有组件名称"""
        names = []
        for name, info in self._neurons.items():
            names.append(name)

        # 按优先级排序
        names.sort(key=lambda x: self._neurons[x]['priority'])
        return names

    def filter_neurons(self,
                       min_priority: Optional[int] = None,
                       max_priority: Optional[int] = None) -> List[Neuron]:
        """根据条件过滤组件"""
        neurons = self.get_all_neurons()

        if min_priority is not None or max_priority is not None:
            filtered_neurons = []
            for neuron in neurons:
                neuron_name = self._get_neuron_name(neuron)
                if neuron_name:
                    priority = self._neurons[neuron_name]['priority']
                    if min_priority is not None and priority < min_priority:
                        continue
                    if max_priority is not None and priority > max_priority:
                        continue
                    filtered_neurons.append(neuron)
            return filtered_neurons

        return neurons

    def _filter_by_namespace(self, neurons: List[Neuron], namespace: str) -> List[Neuron]:
        neuron_config = agent_neuron_factory.get_agent_component_config(namespace)
        # 没注册不过滤
        if not neuron_config:
            return [neuron for neuron in neurons if neuron.name in DEFAULT_COMPONENTS]
        """根据命名空间过滤组件"""
        filtered_neurons = []
        for neuron in neurons:
            neuron_name = self._get_neuron_name(neuron)
            neurons = neuron_config.get('neurons', [])
            if neuron_name in neurons or neurons[0] == Neurons.ALL:
                filtered_neurons.append(neuron)
        return filtered_neurons

    def get_neurons_by_namespace(self, namespace: str) -> List[Neuron]:
        """获取指定命名空间的所有组件"""
        return self.get_all_neurons(namespace=namespace)

    def get_neuron_strategy(self, name: str, namespace: str = None):
        """获取组件的策略配置"""
        neuron_info = self._neurons.get(name)
        if not neuron_info:
            return None

        if namespace and 'strategies' in neuron_info and namespace in neuron_info.get('strategies', {}).keys():
            return neuron_info.get('strategies', {}).get(namespace)

        return neuron_info.get('default_strategy')


# 创建全局工厂实例
neuron_factory = NeuronFactory()
