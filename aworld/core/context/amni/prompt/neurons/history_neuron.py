from typing import List

from ... import ApplicationContext
from . import Neuron
from .neuron_factory import neuron_factory


@neuron_factory.register(name="history", desc="History messages neuron", prio=7)
class HistoryNeuron(Neuron):
    """Neuron for handling historical messages and previous round results related properties"""
    
    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        """Format historical messages and previous round results information"""
        items = []
        
        # Historical messages
        history = context.history
        if history:
            for i, message in enumerate(history):
                items.append(f"<history_message_{i}>{message}</history_message_{i}>")
        
        # Previous round results
        if hasattr(context, 'root') and hasattr(context.root, 'task_state') and hasattr(context.root.task_state, 'previous_round_results'):
            previous_round_results = context.root.task_state.previous_round_results
            if previous_round_results:
                for message in previous_round_results:
                    items.append(f"<previous_round_results>{str(message.to_openai_message())}</previous_round_results>")
        
        return items
    
    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None, **kwargs) -> str:
        """Combine historical messages and previous round results information"""
        if not items:
            items = await self.format_items(context, namespace, **kwargs)
        
        return "<history_messages>\n" + "\n".join(items) + "\n</history_info>\n"
