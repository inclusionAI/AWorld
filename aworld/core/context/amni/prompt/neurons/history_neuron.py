from typing import List

from ... import ApplicationContext
from . import Neuron
from .neuron_factory import neuron_factory


@neuron_factory.register(name="history", desc="History messages neuron", prio=7)
class HistoryNeuron(Neuron):
    """处理历史消息和前一轮结果相关属性的Neuron"""
    
    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        """格式化历史消息和前一轮结果信息"""
        items = []
        
        # 历史消息
        history = context.history
        if history:
            for i, message in enumerate(history):
                items.append(f"<history_message_{i}>{message}</history_message_{i}>")
        
        # 前一轮结果
        if hasattr(context, 'root') and hasattr(context.root, 'task_state') and hasattr(context.root.task_state, 'previous_round_results'):
            previous_round_results = context.root.task_state.previous_round_results
            if previous_round_results:
                for message in previous_round_results:
                    items.append(f"<previous_round_results>{str(message.to_openai_message())}</previous_round_results>")
        
        return items
    
    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None, **kwargs) -> str:
        """组合历史消息和前一轮结果信息"""
        if not items:
            items = await self.format_items(context, namespace, **kwargs)
        
        return "<history_messages>\n" + "\n".join(items) + "\n</history_info>\n"
