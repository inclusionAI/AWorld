from typing import List

from ... import ApplicationContext
from . import Neuron
from .neuron_factory import neuron_factory


@neuron_factory.register(name="conversation_history", desc="Conversation history neuron", prio=3)
class ConversationHistoryNeuron(Neuron):
    """Neuron for entire main conversation history information"""
    
    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        """Format historical messages and previous round results information"""
        items = []
        
        # Historical messages
        history = context.root.history
        if history:
            for i, message in enumerate(history):
                items.append(f"<history_message_{i}>{message.to_openai_message()}</history_message_{i}>")

        return items
    
    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None, **kwargs) -> str:
        """Combine historical messages and previous round results information"""
        if not items:
            items = await self.format_items(context, namespace, **kwargs)
        
        return "<conversation_history>\n" + "\n".join(items) + "\n</conversation_history>\n"
