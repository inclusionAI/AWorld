from typing import List

from amnicontext import ApplicationContext
from . import Neuron


class ConversationHistoryNeuron(Neuron):
    """整个主对话的历史信息Neuron"""
    
    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        """格式化历史消息和前一轮结果信息"""
        items = []
        
        # 历史消息
        history = context.root.history
        if history:
            for i, message in enumerate(history):
                items.append(f"<history_message_{i}>{message.to_openai_message()}</history_message_{i}>")

        return items
    
    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None, **kwargs) -> str:
        """组合历史消息和前一轮结果信息"""
        if not items:
            items = await self.format_items(context, namespace, **kwargs)
        
        return "<conversation_history>\n" + "\n".join(items) + "\n</conversation_history>\n"
